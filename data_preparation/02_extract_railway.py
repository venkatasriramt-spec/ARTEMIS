#!/usr/bin/env python3
"""
ARTEMIS - Step 02: Extract Railway Data from PBF Files
======================================================
Uses pyrosm to parse OpenStreetMap PBF files and extract railway features.
Outputs GeoJSON files with full attribute data, organized by railway type.
"""

import concurrent.futures
import json
import os
import subprocess
import sys
import time
import warnings
import threading
from pathlib import Path

import geopandas as gpd
import pandas as pd
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

console = Console()
print_lock = threading.Lock()

# Suppress shapely deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ----- Configuration -----
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "countries.json"
DEFAULT_PBF_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "geojson"

# Railway tag categories (long-distance/intercity only)
TRACK_TYPES = {"rail", "narrow_gauge"}
STATION_TYPES = {"station", "halt", "stop"}
INFRA_TYPES = {"signal", "switch", "crossing", "level_crossing", "buffer_stop", "turntable"}
SERVICE_TYPES = {"spur", "siding", "yard", "crossover"}

# Columns to preserve from OSM data
KEEP_COLUMNS = [
    "geometry", "id", "osm_type", "railway", "name",
    "operator", "gauge", "electrified", "maxspeed",
    "usage", "service", "tracks", "bridge", "tunnel",
    "ref", "network", "wikipedia", "wikidata",
    "platform", "public_transport", "platforms",
    "station", "railway:preserved", "disused", "abandoned", "construction", "light_rail", "subway", "tram", "bus", "highway", "train"
]


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """Load country configuration from JSON."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_pbf_file(country_code: str, pbf_dir: Path) -> Path | None:
    """Find the PBF file for a given country code."""
    country_dir = pbf_dir / country_code
    if country_dir.exists():
        pbf_files = list(country_dir.glob("*.osm.pbf"))
        if pbf_files:
            return pbf_files[0]

    # Also check directly in pbf_dir
    pbf_files = list(pbf_dir.glob(f"{country_code}*.osm.pbf"))
    if pbf_files:
        return pbf_files[0]

    return None


def extract_railway_data(
    pbf_path: Path,
    railway_types: list[str] | None = None,
) -> gpd.GeoDataFrame | None:
    """
    Extract railway features from a PBF file using pyrosm.
    
    Args:
        pbf_path: Path to the .osm.pbf file
        railway_types: List of railway types to extract (None = all)
    
    Returns:
        GeoDataFrame with railway features, or None if extraction fails
    """
    try:
        from pyrosm import OSM
    except ImportError:
        console.print("[red]Error: pyrosm is not installed. Run: pip install pyrosm[/red]")
        return None

    console.print(f"  [dim]Loading PBF file: {pbf_path.name} ({pbf_path.stat().st_size / 1e9:.2f} GB)[/dim]")

    try:
        osm = OSM(str(pbf_path))

        # Build the custom filter
        if railway_types:
            custom_filter = {"railway": railway_types}
        else:
            custom_filter = {"railway": True}

        console.print(f"  [dim]Extracting railway features (filter: {custom_filter})...[/dim]")

        # Extract using custom criteria
        gdf = osm.get_data_by_custom_criteria(
            custom_filter=custom_filter,
            filter_type="keep",
            keep_nodes=True,
            keep_ways=True,
            keep_relations=True,
            extra_attributes=[c for c in KEEP_COLUMNS if c not in ["geometry", "id", "osm_type", "railway"]]
        )

        if gdf is None or gdf.empty:
            console.print("  [yellow]⚠ No railway features found[/yellow]")
            return None

        console.print(f"  [green]✓ Extracted {len(gdf)} features[/green]")

        # Clean up columns - keep only relevant ones
        available_cols = [c for c in KEEP_COLUMNS if c in gdf.columns]
        # Also keep any column that starts with "railway"
        railway_cols = [c for c in gdf.columns if c.startswith("railway") and c not in available_cols]
        available_cols.extend(railway_cols)

        # Always keep geometry
        if "geometry" not in available_cols:
            available_cols.insert(0, "geometry")

        gdf = gdf[available_cols].copy()

        # Ensure CRS is WGS84
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif gdf.crs.to_epsg() != 4326:
            console.print(f"  [dim]Reprojecting from {gdf.crs} to EPSG:4326[/dim]")
            gdf = gdf.to_crs("EPSG:4326")

        return gdf

    except Exception as e:
        console.print(f"  [red]✗ Extraction error: {e}[/red]")
        return None


def categorize_features(gdf: gpd.GeoDataFrame) -> dict[str, gpd.GeoDataFrame]:
    """
    Split a GeoDataFrame into categories based on the 'railway' tag.
    
    Returns:
        Dict mapping category name to GeoDataFrame subset
    """
    categories = {}

    if "railway" not in gdf.columns:
        categories["all"] = gdf
        return categories

    # Categorize each feature
    for railway_val in gdf["railway"].unique():
        if pd.isna(railway_val):
            continue

        mask = gdf["railway"] == railway_val

        if railway_val in TRACK_TYPES:
            category = "tracks"
        elif railway_val in STATION_TYPES:
            category = "stations"
        elif railway_val in INFRA_TYPES:
            category = "infrastructure"
        elif railway_val in SERVICE_TYPES:
            category = "service"
        else:
            category = "other"

        if category not in categories:
            categories[category] = gdf[mask].copy()
        else:
            categories[category] = pd.concat(
                [categories[category], gdf[mask]], ignore_index=True
            )

    return categories


def save_geojson(
    gdf: gpd.GeoDataFrame,
    output_path: Path,
    simplify_tolerance: float | None = None,
) -> int:
    """
    Save a GeoDataFrame to GeoJSON format.
    
    Args:
        gdf: GeoDataFrame to save
        output_path: Output file path
        simplify_tolerance: Optional geometry simplification tolerance (degrees)
    
    Returns:
        Number of features saved
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if simplify_tolerance and simplify_tolerance > 0:
        gdf = gdf.copy()
        gdf["geometry"] = gdf["geometry"].simplify(simplify_tolerance)

    # Convert any non-serializable types to strings
    for col in gdf.columns:
        if col == "geometry":
            continue
        if gdf[col].dtype == "object":
            gdf[col] = gdf[col].apply(
                lambda x: str(x) if isinstance(x, (list, dict, set)) else x
            )

    gdf.to_file(str(output_path), driver="GeoJSON")
    return len(gdf)


def prefilter_with_osmium(input_pbf: Path, output_pbf: Path) -> bool:
    """
    Use osmium-tool to stream-filter the PBF file for railway features.
    This shrinks a massive PBF file into a tiny one to prevent OOM errors in pyrosm.
    """
    cmd = [
        "osmium", "tags-filter", str(input_pbf),
        "nwr/railway",
        "-o", str(output_pbf),
        "--overwrite"
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8").strip() if e.stderr else str(e)
        console.print(f"  [red]Osmium error: {error_msg}[/red]")
        return False
    except FileNotFoundError:
        console.print("  [red]osmium-tool not found. Please install it (e.g. sudo apt-get install osmium-tool).[/red]")
        return False


def extract_country(
    country: dict,
    pbf_dir: Path,
    output_dir: Path,
    railway_types: list[str] | None = None,
    categorize: bool = True,
) -> dict:
    """
    Extract railway data for a single country.
    
    Returns:
        Status dict with extraction results
    """
    country_name = country["name"]
    country_code = country["code"]

    result = {
        "country": country_name,
        "code": country_code,
        "status": "unknown",
        "features_total": 0,
        "categories": {},
        "output_files": [],
        "duration_s": 0,
        "error": None,
    }

    start_time = time.time()

    # Find PBF file
    pbf_path = find_pbf_file(country_code, pbf_dir)
    if not pbf_path:
        result["status"] = "failed"
        result["error"] = f"PBF file not found in {pbf_dir / country_code}"
        return result

    # Pre-filter with Osmium bypassed (we have 96GB RAM, raw extraction is fine)
    filtered_pbf = pbf_path

    # Extract railway data from the tiny filtered file
    gdf = extract_railway_data(filtered_pbf, railway_types)
    
    # Removed cleanup to prevent deleting the raw PBF

    if gdf is None or gdf.empty:
        result["status"] = "no_data"
        result["error"] = "No railway features extracted"
        result["duration_s"] = time.time() - start_time
        return result

    result["features_total"] = len(gdf)

    # Output directory for this country
    country_output = output_dir / country_code
    country_output.mkdir(parents=True, exist_ok=True)

    if categorize:
        # Save categorized GeoJSON files
        categories = categorize_features(gdf)
        for category_name, category_gdf in categories.items():
            output_file = country_output / f"{country_code}_{category_name}.geojson"
            count = save_geojson(category_gdf, output_file)
            result["categories"][category_name] = count
            result["output_files"].append(str(output_file))

    combined_file = country_output / f"{country_code}_all_railway.geojson"
    save_geojson(gdf, combined_file)
    result["output_files"].append(str(combined_file))

    # Extract platform features
    try:
        from pyrosm import OSM
        osm = OSM(str(filtered_pbf))
        console.print("  [dim]Extracting platform features...[/dim]")
        platforms_gdf = osm.get_data_by_custom_criteria(
            custom_filter={"railway": ["platform", "platform_edge"], "public_transport": ["platform"]},
            filter_type="keep",
            keep_nodes=True,
            keep_ways=True,
            keep_relations=True,
            extra_attributes=["name", "ref", "train", "bus", "highway"]
        )
        if platforms_gdf is not None and not platforms_gdf.empty:
            mask = ~platforms_gdf.get("bus", pd.Series(dtype=str)).isin(["yes"])
            if "highway" in platforms_gdf.columns:
                mask &= platforms_gdf["highway"].isna()
            
            is_railway = platforms_gdf.get("railway", pd.Series(dtype=str)).notna()
            is_train = platforms_gdf.get("train", pd.Series(dtype=str)).isin(["yes"])
            is_pt = platforms_gdf.get("public_transport", pd.Series(dtype=str)) == "platform"
            
            valid_pt = ~is_pt | is_railway | is_train
            
            platforms_gdf = platforms_gdf[mask & valid_pt].copy()
            if not platforms_gdf.empty:
                platforms_file = country_output / f"{country_code}_platforms.geojson"
                save_geojson(platforms_gdf, platforms_file)
                result["output_files"].append(str(platforms_file))
                console.print(f"  [green]✓ Extracted {len(platforms_gdf)} platforms[/green]")
    except Exception as e:
        console.print(f"  [red]Failed to extract platforms: {e}[/red]")

    result["status"] = "extracted"
    result["duration_s"] = time.time() - start_time
    return result


def extract_all(
    config: dict,
    pbf_dir: Path = DEFAULT_PBF_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    countries: list[str] | None = None,
    railway_types: list[str] | None = None,
    max_workers: int = 4,
) -> list[dict]:
    """
    Extract railway data for all (or selected) countries.
    
    Args:
        config: Loaded configuration dict
        pbf_dir: Directory containing downloaded PBF files
        output_dir: Output directory for GeoJSON files
        countries: List of country codes to process (None = all)
        railway_types: Railway types to extract (None = use config defaults)
    
    Returns:
        List of status dicts for each country
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    if railway_types is None:
        railway_types = config.get("default_extract_types")

    country_list = config["countries"]
    if countries:
        country_list = [c for c in country_list if c["code"] in countries]

    total = len(country_list)
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]ARTEMIS - Railway Data Extraction[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[dim]Countries: {total} | Railway types: {railway_types}[/dim]")
    console.print(f"[dim]Input: {pbf_dir} | Output: {output_dir} | Workers: {max_workers}[/dim]\n")

    def process_country(country):
        return extract_country(
            country=country,
            pbf_dir=pbf_dir,
            output_dir=output_dir,
            railway_types=railway_types,
        )

    # Use ProcessPoolExecutor for CPU-bound extraction tasks
    # (Using ProcessPoolExecutor avoids GIL, but ThreadPoolExecutor is used here for simplicity with threading locks, pyrosm mostly drops GIL)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_country, country): country
            for country in country_list
        }

        for future in tqdm(concurrent.futures.as_completed(futures), total=total, desc="Extracting countries", unit="country"):
            result = future.result()
            results.append(result)
    
    console.print("")

    # Print summary
    _print_summary(results)

    # Save report
    report_path = output_dir / "extraction_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    console.print(f"\n[dim]Report saved to: {report_path}[/dim]")

    return results


def _print_summary(results: list[dict]) -> None:
    """Print a summary table of extraction results."""
    table = Table(title="Extraction Summary")
    table.add_column("Country", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Features", justify="right")
    table.add_column("Tracks", justify="right")
    table.add_column("Stations", justify="right")
    table.add_column("Duration", justify="right")

    for r in results:
        status_style = {
            "extracted": "[green]✓ OK[/green]",
            "no_data": "[yellow]⚠ Empty[/yellow]",
            "failed": "[red]✗ Failed[/red]",
        }.get(r["status"], r["status"])

        features = str(r["features_total"]) if r["features_total"] else "-"
        tracks = str(r["categories"].get("tracks", "-"))
        stations = str(r["categories"].get("stations", "-"))
        duration = f"{r['duration_s']:.1f}s" if r["duration_s"] else "-"

        table.add_row(r["country"], status_style, features, tracks, stations, duration)

    console.print(table)

    total_features = sum(r["features_total"] for r in results)
    extracted = sum(1 for r in results if r["status"] == "extracted")
    failed = sum(1 for r in results if r["status"] in ("failed", "no_data"))

    console.print(
        f"\n[bold]Total: {extracted} extracted, {failed} failed | "
        f"{total_features:,} features total[/bold]"
    )


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ARTEMIS - Extract railway data from PBF files"
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        help="Country codes to process (e.g., us uk germany). Default: all",
    )
    parser.add_argument(
        "--pbf-dir",
        type=Path,
        default=DEFAULT_PBF_DIR,
        help=f"PBF files directory (default: {DEFAULT_PBF_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--railway-types",
        nargs="+",
        help="Railway types to extract (e.g., rail subway station). Default: from config",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Config file path (default: {CONFIG_PATH})",
    )

    args = parser.parse_args()

    config = load_config(args.config)
    results = extract_all(
        config=config,
        pbf_dir=args.pbf_dir,
        output_dir=args.output_dir,
        countries=args.countries,
        railway_types=args.railway_types,
    )

    failed = sum(1 for r in results if r["status"] in ("failed", "no_data"))
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
