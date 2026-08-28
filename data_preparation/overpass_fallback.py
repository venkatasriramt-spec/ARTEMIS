#!/usr/bin/env python3
"""
ARTEMIS - Overpass API Fallback Client
======================================
Alternative data extraction using the Overpass API for:
- Small countries where Geofabrik is unavailable
- Targeted sub-region queries
- Real-time data updates

Implements:
- Exponential backoff with jitter
- Rate limit awareness via /api/status
- Sub-region chunking for large countries
- GeoJSON output compatible with the main pipeline
"""

import hashlib
import json
import math
import os
import random
import sys
import time
import threading
import concurrent.futures
from pathlib import Path

import requests
from rich.console import Console
from tqdm import tqdm

console = Console()
print_lock = threading.Lock()

# ----- Configuration -----
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "countries.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "geojson"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "overpass_cache"

OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_STATUS_URL = "https://overpass-api.de/api/status"


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """Load country configuration from JSON."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_api_status() -> dict:
    """
    Check the Overpass API status.
    
    Returns:
        Dict with 'available_slots', 'rate_limit', 'wait_seconds'
    """
    try:
        response = requests.get(OVERPASS_STATUS_URL, timeout=10)
        text = response.text

        status = {
            "available_slots": 0,
            "rate_limit": False,
            "wait_seconds": 0,
            "raw": text,
        }

        # Parse status text for slot availability
        for line in text.split("\n"):
            line = line.strip()
            if "available now" in line.lower():
                status["available_slots"] += 1
            elif "available after" in line.lower():
                # Parse wait time
                try:
                    parts = line.split("after")[-1].strip()
                    # Try to extract seconds
                    if "seconds" in parts:
                        seconds = int("".join(filter(str.isdigit, parts.split("seconds")[0])))
                        status["wait_seconds"] = max(status["wait_seconds"], seconds)
                except (ValueError, IndexError):
                    status["wait_seconds"] = max(status["wait_seconds"], 30)
            elif "rate limit" in line.lower():
                status["rate_limit"] = True

        return status

    except requests.RequestException as e:
        return {
            "available_slots": 0,
            "rate_limit": True,
            "wait_seconds": 60,
            "raw": str(e),
        }


def wait_for_slot(max_wait: int = 300) -> bool:
    """
    Wait until an API slot is available.
    
    Returns:
        True if a slot became available, False if max_wait exceeded
    """
    total_waited = 0

    while total_waited < max_wait:
        status = check_api_status()

        if status["available_slots"] > 0:
            return True

        wait_time = max(status["wait_seconds"], 10)
        wait_time = min(wait_time, max_wait - total_waited)

        if wait_time <= 0:
            break

        console.print(f"  [dim]  Waiting {wait_time}s for API slot... ({total_waited}s elapsed)[/dim]")
        time.sleep(wait_time)
        total_waited += wait_time

    return False


def build_railway_query(
    area_name: str | None = None,
    area_id: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    railway_types: list[str] | None = None,
    timeout: int = 900,
    maxsize: int = 1073741824,
) -> str:
    """
    Build an Overpass QL query for railway data.
    
    Args:
        area_name: Country/region name for area lookup
        area_id: Overpass area ID (3600000000 + OSM relation ID)
        bbox: Bounding box (south, west, north, east)
        railway_types: List of railway types to query
        timeout: Query timeout in seconds
        maxsize: Maximum response size in bytes
    
    Returns:
        Overpass QL query string
    """
    # Build the filter
    if railway_types:
        railway_filter = "|".join(railway_types)
        way_filter = f'way["railway"~"{railway_filter}"]'
        node_filter = f'node["railway"~"{railway_filter}"]'
        rel_filter = f'relation["railway"~"{railway_filter}"]'
    else:
        way_filter = 'way["railway"]'
        node_filter = 'node["railway"]'
        rel_filter = 'relation["railway"]'

    # Build area/bbox constraint
    if area_id:
        area_clause = f"area({area_id})->.searchArea;"
        area_ref = "(area.searchArea)"
    elif area_name:
        area_clause = f'area["name"="{area_name}"]["admin_level"="2"]->.searchArea;'
        area_ref = "(area.searchArea)"
    elif bbox:
        area_clause = ""
        s, w, n, e = bbox
        area_ref = f"({s},{w},{n},{e})"
    else:
        raise ValueError("Must provide area_name, area_id, or bbox")

    query = f"""[out:json][timeout:{timeout}][maxsize:{maxsize}];
{area_clause}
(
  {node_filter}{area_ref};
  {way_filter}{area_ref};
  {rel_filter}{area_ref};
);
out body;
>;
out skel qt;
"""
    return query


def execute_query(
    query: str,
    max_retries: int = 5,
    initial_backoff: float = 30,
    max_backoff: float = 300,
    cache_dir: Path | None = None,
) -> dict | None:
    """
    Execute an Overpass API query with retry logic.
    
    Returns:
        Parsed JSON response or None on failure
    """
    # Check cache
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.md5(query.encode()).hexdigest()
        cache_file = cache_dir / f"{cache_key}.json"

        if cache_file.exists():
            console.print(f"  [dim]  Cache hit: {cache_key[:12]}...[/dim]")
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)

    for attempt in range(1, max_retries + 1):
        try:
            # Wait for available slot
            if not wait_for_slot():
                console.print("  [yellow]⚠ No API slots available, retrying later...[/yellow]")
                time.sleep(initial_backoff)
                continue

            console.print(f"  [dim]  Sending query (attempt {attempt}/{max_retries})...[/dim]")

            response = requests.post(
                OVERPASS_API_URL,
                data={"data": query},
                timeout=960,  # Slightly longer than query timeout
            )

            if response.status_code == 200:
                data = response.json()

                # Cache the result
                if cache_dir:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f)

                return data

            elif response.status_code == 429:
                # Rate limited
                console.print(f"  [yellow]⚠ Rate limited (429). Backing off...[/yellow]")

            elif response.status_code == 504:
                # Gateway timeout
                console.print(f"  [yellow]⚠ Gateway timeout (504). Query too large?[/yellow]")

            else:
                console.print(f"  [yellow]⚠ HTTP {response.status_code}: {response.text[:200]}[/yellow]")

        except requests.Timeout:
            console.print(f"  [yellow]⚠ Request timed out[/yellow]")
        except requests.RequestException as e:
            console.print(f"  [yellow]⚠ Request error: {e}[/yellow]")

        # Exponential backoff with jitter
        backoff = min(initial_backoff * (2 ** (attempt - 1)), max_backoff)
        jitter = random.uniform(0, backoff * 0.3)
        wait = backoff + jitter

        if attempt < max_retries:
            console.print(f"  [dim]  Retrying in {wait:.0f}s...[/dim]")
            time.sleep(wait)

    console.print(f"  [red]✗ All {max_retries} attempts failed[/red]")
    return None


def osm_json_to_geojson(osm_data: dict) -> dict:
    """
    Convert Overpass JSON response to GeoJSON format.
    
    The Overpass API returns OSM-specific JSON which needs to be
    converted to standard GeoJSON for compatibility with the pipeline.
    """
    features = []
    nodes = {}

    # First pass: collect all nodes with coordinates
    for element in osm_data.get("elements", []):
        if element["type"] == "node":
            nodes[element["id"]] = {
                "lat": element.get("lat"),
                "lon": element.get("lon"),
            }

            # If the node has railway tags, add it as a Point feature
            tags = element.get("tags", {})
            if "railway" in tags:
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [element["lon"], element["lat"]],
                    },
                    "properties": {
                        "id": element["id"],
                        "osm_type": "node",
                        **tags,
                    },
                }
                features.append(feature)

    # Second pass: process ways
    for element in osm_data.get("elements", []):
        if element["type"] == "way":
            tags = element.get("tags", {})
            node_refs = element.get("nodes", [])

            # Build coordinate list from node references
            coords = []
            for node_id in node_refs:
                if node_id in nodes:
                    node = nodes[node_id]
                    if node["lat"] is not None and node["lon"] is not None:
                        coords.append([node["lon"], node["lat"]])

            if len(coords) >= 2:
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords,
                    },
                    "properties": {
                        "id": element["id"],
                        "osm_type": "way",
                        **tags,
                    },
                }
                features.append(feature)

    # Third pass: process relations (simplified - extract member ways)
    for element in osm_data.get("elements", []):
        if element["type"] == "relation":
            tags = element.get("tags", {})
            if "railway" in tags:
                # Add relation as a feature with metadata only
                # (geometry comes from member ways already processed)
                feature = {
                    "type": "Feature",
                    "geometry": None,  # Will be resolved by member ways
                    "properties": {
                        "id": element["id"],
                        "osm_type": "relation",
                        **tags,
                    },
                }
                # Only add if it has useful metadata
                if any(k in tags for k in ["name", "operator", "network", "ref"]):
                    # Create a dummy point for the relation
                    members = element.get("members", [])
                    for member in members:
                        if member.get("type") == "node" and member.get("ref") in nodes:
                            node = nodes[member["ref"]]
                            if node["lat"] and node["lon"]:
                                feature["geometry"] = {
                                    "type": "Point",
                                    "coordinates": [node["lon"], node["lat"]],
                                }
                                break

                    if feature["geometry"]:
                        features.append(feature)

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    return geojson


def query_country(
    country: dict,
    output_dir: Path,
    railway_types: list[str] | None = None,
    cache_dir: Path | None = None,
) -> dict:
    """
    Query railway data for a country via Overpass API.
    
    Returns:
        Status dict with query results
    """
    country_name = country["name"]
    country_code = country["code"]
    area_id = country.get("overpass_area_id")
    area_name = country.get("overpass_area_name")

    result = {
        "country": country_name,
        "code": country_code,
        "status": "unknown",
        "features": 0,
        "output_file": None,
        "duration_s": 0,
        "error": None,
    }

    start_time = time.time()

    # Build query
    query = build_railway_query(
        area_name=area_name if not area_id else None,
        area_id=area_id,
        railway_types=railway_types,
    )

    # Execute query
    osm_data = execute_query(
        query=query,
        cache_dir=cache_dir,
    )

    if osm_data is None:
        result["status"] = "failed"
        result["error"] = "All query attempts failed"
        result["duration_s"] = time.time() - start_time
        return result

    # Convert to GeoJSON
    geojson = osm_json_to_geojson(osm_data)

    num_features = len(geojson.get("features", []))
    if num_features == 0:
        result["status"] = "no_data"
        result["error"] = "No railway features found"
        result["duration_s"] = time.time() - start_time
        return result

    # Save GeoJSON
    country_output = output_dir / country_code
    country_output.mkdir(parents=True, exist_ok=True)
    output_file = country_output / f"{country_code}_all_railway.geojson"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(geojson, f)

    result["status"] = "extracted"
    result["features"] = num_features
    result["output_file"] = str(output_file)
    result["duration_s"] = time.time() - start_time

    return result


def query_all(
    config: dict,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    countries: list[str] | None = None,
    railway_types: list[str] | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    max_workers: int = 4,
) -> list[dict]:
    """
    Query railway data for all (or selected) countries via Overpass API.
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
    console.print(f"[bold cyan]ARTEMIS - Overpass API Extraction (Fallback)[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[dim]Countries: {total} | Railway types: {railway_types} | Workers: {max_workers}[/dim]\n")

    console.print("[yellow]⚠ WARNING: Overpass API may timeout for large countries (US, Russia, China).[/yellow]")
    console.print("[yellow]  Consider using the Geofabrik pipeline (01_download_pbf.py → 02_extract_railway.py) instead.[/yellow]\n")

    def process_country(country):
        # Initial sleep to avoid stampeding the API
        time.sleep(random.uniform(1, 5))
        return query_country(
            country=country,
            output_dir=output_dir,
            railway_types=railway_types,
            cache_dir=cache_dir,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_country, country): country for country in country_list}
        
        for future in tqdm(concurrent.futures.as_completed(futures), total=total, desc="Overpass extraction", unit="country"):
            result = future.result()
            results.append(result)
                    
    console.print("")
    return results


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ARTEMIS - Overpass API fallback for railway data extraction"
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        help="Country codes to query (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--railway-types",
        nargs="+",
        help="Railway types to extract (default: from config)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable query caching",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
    )

    args = parser.parse_args()

    config = load_config(args.config)
    cache_dir = None if args.no_cache else DEFAULT_CACHE_DIR

    results = query_all(
        config=config,
        output_dir=args.output_dir,
        countries=args.countries,
        railway_types=args.railway_types,
        cache_dir=cache_dir,
    )

    failed = sum(1 for r in results if r["status"] in ("failed", "no_data"))
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
