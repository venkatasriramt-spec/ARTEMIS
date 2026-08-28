#!/usr/bin/env python3
"""
ARTEMIS - Step 03: Convert GeoJSON to KML
==========================================
Converts extracted GeoJSON railway data to KML format with:
- Color-coded styling per railway type
- Organized folder structure within KML
- ExtendedData for all railway attributes
- Full attribute preservation for simulation use
"""

import concurrent.futures
import json
import sys
import time
import threading
import html
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point, LineString, MultiLineString, Polygon, MultiPolygon
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

console = Console()
print_lock = threading.Lock()

# ----- Configuration -----
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "countries.json"
DEFAULT_GEOJSON_DIR = PROJECT_ROOT / "data" / "processed" / "geojson"
DEFAULT_KML_DIR = PROJECT_ROOT / "data" / "processed" / "kml"

# KML Style definitions (AABBGGRR format — alpha, blue, green, red)
# Focused on long-distance/intercity railway only
RAILWAY_STYLES = {
    "rail": {
        "color": "ff0000ff",      # Red - mainline rail
        "width": 3,
        "icon": "http://maps.google.com/mapfiles/kml/shapes/rail.png",
        "description": "Mainline Railway Track",
    },
    "narrow_gauge": {
        "color": "ff00aaff",      # Yellow-Orange - narrow gauge
        "width": 2,
        "icon": "http://maps.google.com/mapfiles/kml/shapes/rail.png",
        "description": "Narrow Gauge Railway",
    },
    "station": {
        "color": "ff0088ff",      # Dark Orange - station
        "width": 0,
        "icon": "http://maps.google.com/mapfiles/kml/shapes/rail.png",
        "description": "Railway Station",
    },
    "halt": {
        "color": "ff00ccff",      # Light Orange - halt
        "width": 0,
        "icon": "http://maps.google.com/mapfiles/kml/paddle/wht-blank.png",
        "description": "Railway Halt/Stop",
    },
    "default": {
        "color": "ff888888",      # Grey - other
        "width": 1,
        "icon": "http://maps.google.com/mapfiles/kml/paddle/wht-circle.png",
        "description": "Railway Feature",
    },
}

# Folder organization mapping (intercity rail only)
FOLDER_MAP = {
    "tracks": ["rail", "narrow_gauge"],
    "stations": ["station", "halt", "stop"],
    "infrastructure": ["signal", "switch", "crossing", "level_crossing", "buffer_stop"],
    "service": ["spur", "siding", "yard"],
}


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """Load country configuration from JSON."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_style(railway_type: str) -> dict:
    """Get KML style for a railway type."""
    return RAILWAY_STYLES.get(railway_type, RAILWAY_STYLES["default"])


def get_folder_name(railway_type: str) -> str:
    """Determine which folder a railway type belongs to."""
    for folder, types in FOLDER_MAP.items():
        if railway_type in types:
            return folder
    return "other"


def format_coords(coords) -> str:
    """Format Shapely coordinates to KML string format."""
    return " ".join([f"{x},{y},0" for x, y in coords])


def get_style_xml() -> str:
    """Generate KML <Style> definitions."""
    xml = []
    for rtype, style_def in RAILWAY_STYLES.items():
        xml.append(f'''    <Style id="{rtype}">
      <LineStyle>
        <color>{style_def["color"]}</color>
        <width>{style_def["width"]}</width>
      </LineStyle>
      <IconStyle>
        <scale>0.8</scale>
        <Icon><href>{html.escape(style_def["icon"])}</href></Icon>
      </IconStyle>
      <LabelStyle><scale>0.7</scale></LabelStyle>
    </Style>''')
    return "\n".join(xml)


def generate_geometry_xml(geometry) -> str:
    """Convert a Shapely geometry into KML Geometry XML."""
    if geometry is None or geometry.is_empty:
        return ""
    
    if isinstance(geometry, Point):
        return f"<Point><coordinates>{geometry.x},{geometry.y},0</coordinates></Point>"
    elif isinstance(geometry, LineString):
        coords = format_coords(geometry.coords)
        return f"<LineString><altitudeMode>clampToGround</altitudeMode><coordinates>{coords}</coordinates></LineString>"
    elif isinstance(geometry, MultiLineString):
        xml = ["<MultiGeometry>"]
        for line in geometry.geoms:
            coords = format_coords(line.coords)
            xml.append(f"<LineString><altitudeMode>clampToGround</altitudeMode><coordinates>{coords}</coordinates></LineString>")
        xml.append("</MultiGeometry>")
        return "".join(xml)
    elif isinstance(geometry, Polygon):
        coords = format_coords(geometry.exterior.coords)
        return f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
    elif isinstance(geometry, MultiPolygon):
        xml = ["<MultiGeometry>"]
        for poly in geometry.geoms:
            coords = format_coords(poly.exterior.coords)
            xml.append(f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon>")
        xml.append("</MultiGeometry>")
        return "".join(xml)
    return ""


def geojson_to_kml(
    geojson_path: Path,
    kml_path: Path,
    country_name: str,
    organize_folders: bool = True,
) -> dict:
    """
    Convert a GeoJSON file to KML format securely by streaming directly to disk.
    
    Args:
        geojson_path: Path to input GeoJSON file
        kml_path: Path to output KML file
        country_name: Name of the country (used in KML document name)
        organize_folders: Organize features into folders by type
    
    Returns:
        Status dict with conversion results
    """
    result = {
        "input": str(geojson_path),
        "output": str(kml_path),
        "features": 0,
        "status": "unknown",
        "error": None,
    }

    try:
        # Read GeoJSON
        gdf = gpd.read_file(str(geojson_path))

        if gdf.empty:
            result["status"] = "empty"
            result["error"] = "GeoJSON file is empty"
            return result

        # Ensure WGS84
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")

        result["features"] = len(gdf)
        kml_path.parent.mkdir(parents=True, exist_ok=True)

        # Build groupings
        if organize_folders:
            folders = {
                "tracks": [],
                "stations": [],
                "infrastructure": [],
                "service": [],
                "other": [],
            }
            for idx, row in gdf.iterrows():
                rtype = row.get("railway", "unknown") if "railway" in gdf.columns else "unknown"
                folder_key = get_folder_name(str(rtype))
                folders[folder_key].append((idx, row))
        else:
            folders = {"all": [(idx, row) for idx, row in gdf.iterrows()]}

        with open(kml_path, "w", encoding="utf-8") as f:
            # Write Header
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<kml xmlns="http://www.opengis.net/kml/2.2">\n')
            f.write('  <Document>\n')
            f.write(f'    <name>{html.escape(f"ARTEMIS - {country_name} Railway Network")}</name>\n')
            desc = (
                f"Railway network data for {country_name}\\n"
                f"Generated by ARTEMIS\\n"
                f"Source: OpenStreetMap via Geofabrik\\n"
                f"Features: {len(gdf)}"
            )
            f.write(f'    <description>{html.escape(desc)}</description>\n')
            f.write(get_style_xml() + "\n")

            # Write Folders and Features
            for folder_name, features in folders.items():
                if not features:
                    continue
                
                f.write(f'    <Folder>\n')
                f.write(f'      <name>{html.escape(folder_name.title())}</name>\n')
                
                for idx, row in features:
                    geometry = row.geometry
                    if geometry is None or geometry.is_empty:
                        continue
                        
                    railway_type = str(row.get("railway", "unknown") if "railway" in gdf.columns else "unknown")
                    
                    name = row.get("name", "") if "name" in gdf.columns else ""
                    if not name or str(name) == "nan":
                        name = f"{railway_type}_{idx}"
                    
                    # Style
                    style_id = railway_type if railway_type in RAILWAY_STYLES else "default"
                    
                    # Description
                    desc_parts = [f"<b>Type:</b> {html.escape(railway_type)}"]
                    for col in ["operator", "gauge", "electrified", "maxspeed", "usage", "tracks", "network"]:
                        if col in gdf.columns:
                            val = row.get(col)
                            if val and str(val) != "nan":
                                desc_parts.append(f"<b>{col.title()}:</b> {html.escape(str(val))}")
                    description = "<br/>".join(desc_parts)

                    f.write('      <Placemark>\n')
                    f.write(f'        <name>{html.escape(str(name))}</name>\n')
                    f.write(f'        <description><![CDATA[{description}]]></description>\n')
                    f.write(f'        <styleUrl>#{style_id}</styleUrl>\n')
                    
                    # Extended Data
                    f.write('        <ExtendedData>\n')
                    for col in gdf.columns:
                        if col == "geometry":
                            continue
                        val = row.get(col)
                        if val is not None and str(val) != "nan":
                            f.write(f'          <Data name="{html.escape(str(col))}"><value>{html.escape(str(val))}</value></Data>\n')
                    f.write('        </ExtendedData>\n')
                    
                    # Geometry
                    geom_xml = generate_geometry_xml(geometry)
                    f.write(f'        {geom_xml}\n')
                    f.write('      </Placemark>\n')
                
                f.write(f'    </Folder>\n')
            
            # Write Footer
            f.write('  </Document>\n')
            f.write('</kml>\n')

        result["status"] = "converted"
        result["output_size_mb"] = kml_path.stat().st_size / 1e6

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)

    return result


def convert_country(
    country_code: str,
    country_name: str,
    geojson_dir: Path,
    kml_dir: Path,
    combined_only: bool = False,
) -> dict:
    """
    Convert all GeoJSON files for a country to KML.
    
    Args:
        country_code: Country code (e.g., 'us')
        country_name: Full country name
        geojson_dir: Base directory containing GeoJSON files
        kml_dir: Base output directory for KML files
        combined_only: If True, only convert the combined file
    
    Returns:
        Status dict with conversion results
    """
    country_geojson_dir = geojson_dir / country_code
    country_kml_dir = kml_dir / country_code
    country_kml_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "country": country_name,
        "code": country_code,
        "status": "unknown",
        "files_converted": 0,
        "total_features": 0,
        "output_files": [],
        "duration_s": 0,
        "error": None,
    }

    start_time = time.time()

    if not country_geojson_dir.exists():
        result["status"] = "failed"
        result["error"] = f"GeoJSON directory not found: {country_geojson_dir}"
        return result

    # Find GeoJSON files
    geojson_files = list(country_geojson_dir.glob("*.geojson"))
    if not geojson_files:
        result["status"] = "failed"
        result["error"] = "No GeoJSON files found"
        return result

    if combined_only:
        # Only process the combined file
        combined = [f for f in geojson_files if "all_railway" in f.name]
        if combined:
            geojson_files = combined
        else:
            geojson_files = geojson_files[:1]  # Fallback to first file

    for geojson_file in geojson_files:
        kml_filename = geojson_file.stem + ".kml"
        kml_path = country_kml_dir / kml_filename

        file_result = geojson_to_kml(
            geojson_path=geojson_file,
            kml_path=kml_path,
            country_name=country_name,
        )

        if file_result["status"] == "converted":
            result["files_converted"] += 1
            result["total_features"] += file_result["features"]
            result["output_files"].append(str(kml_path))

    result["status"] = "converted" if result["files_converted"] > 0 else "failed"
    result["duration_s"] = time.time() - start_time
    return result


def convert_all(
    config: dict,
    geojson_dir: Path = DEFAULT_GEOJSON_DIR,
    kml_dir: Path = DEFAULT_KML_DIR,
    countries: list[str] | None = None,
    combined_only: bool = False,
    max_workers: int = 16,
) -> list[dict]:
    """
    Convert GeoJSON to KML for all (or selected) countries.
    """
    kml_dir.mkdir(parents=True, exist_ok=True)
    results = []

    country_list = config["countries"]
    if countries:
        country_list = [c for c in country_list if c["code"] in countries]

    total = len(country_list)
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]ARTEMIS - GeoJSON to KML Conversion[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[dim]Countries: {total} | Input: {geojson_dir} | Output: {kml_dir} | Workers: {max_workers}[/dim]\n")

    # Use ProcessPoolExecutor for CPU-bound KML generation tasks
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                convert_country,
                country["code"],
                country["name"],
                geojson_dir,
                kml_dir,
                combined_only
            ): country
            for country in country_list
        }

        for future in tqdm(concurrent.futures.as_completed(futures), total=total, desc="Converting GeoJSON to KML", unit="country"):
            result = future.result()
            results.append(result)
    
    console.print("")

    # Print summary
    _print_summary(results)

    # Save report
    report_path = kml_dir / "conversion_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    console.print(f"\n[dim]Report saved to: {report_path}[/dim]")

    return results


def _print_summary(results: list[dict]) -> None:
    """Print a summary table."""
    table = Table(title="KML Conversion Summary")
    table.add_column("Country", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Files", justify="right")
    table.add_column("Features", justify="right")
    table.add_column("Duration", justify="right")

    for r in results:
        status_style = {
            "converted": "[green]✓ OK[/green]",
            "failed": "[red]✗ Failed[/red]",
        }.get(r["status"], r["status"])

        table.add_row(
            r["country"],
            status_style,
            str(r["files_converted"]),
            f"{r['total_features']:,}",
            f"{r['duration_s']:.1f}s",
        )

    console.print(table)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ARTEMIS - Convert GeoJSON to KML"
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        help="Country codes to convert (default: all)",
    )
    parser.add_argument(
        "--geojson-dir",
        type=Path,
        default=DEFAULT_GEOJSON_DIR,
        help=f"GeoJSON input directory (default: {DEFAULT_GEOJSON_DIR})",
    )
    parser.add_argument(
        "--kml-dir",
        type=Path,
        default=DEFAULT_KML_DIR,
        help=f"KML output directory (default: {DEFAULT_KML_DIR})",
    )
    parser.add_argument(
        "--combined-only",
        action="store_true",
        help="Only convert the combined 'all_railway' file per country",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Config file path (default: {CONFIG_PATH})",
    )

    args = parser.parse_args()

    config = load_config(args.config)
    results = convert_all(
        config=config,
        geojson_dir=args.geojson_dir,
        kml_dir=args.kml_dir,
        countries=args.countries,
        combined_only=args.combined_only,
    )

    failed = sum(1 for r in results if r["status"] == "failed")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
