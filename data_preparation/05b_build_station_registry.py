import os
import json
import random
import logging
from pathlib import Path
import geopandas as gpd
import sys

import importlib.util

# Load core_engine/06_spatial_routing.py
spec = importlib.util.spec_from_file_location("spatial_routing", str(Path(__file__).resolve().parent.parent / "core_engine" / "06_spatial_routing.py"))
spatial_routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spatial_routing)
RailwayRouter = spatial_routing.RailwayRouter
logger = logging.getLogger(__name__)

def build_station_registry(country_code="uk"):
    base_dir = Path(__file__).resolve().parent.parent
    geojson_dir = base_dir / "data" / "processed" / "geojson" / country_code
    stations_file = geojson_dir / f"{country_code}_stations.geojson"
    out_file = geojson_dir / "stations.json"

    if not stations_file.exists():
        logger.error(f"Stations file not found: {stations_file}")
        return

    logger.info(f"Loading {country_code} stations from {stations_file}...")
    gdf = gpd.read_file(stations_file)
    
    logger.info("Initializing RailwayRouter to snap stations to nodes...")
    router = RailwayRouter(country_code)
    
    registry = {}
    
    # We want a stable seed for the jitter
    random.seed(42)

    for idx, row in gdf.iterrows():
        name = row.get("name")
        if not name or str(name) == "nan":
            name = f"Station #{idx}"
            
        lon = row.geometry.x
        lat = row.geometry.y
        
        # Snap to nearest node in the graph
        try:
            node_id, node_data, dist_km = router.find_nearest_node(lat, lon)
        except Exception as e:
            logger.warning(f"Could not snap station {name}: {e}")
            continue
            
        if dist_km > 5.0:
            # Too far from the tracks (abandoned station?)
            continue
            
        # Extract real platform count if tagged
        platform_val = row.get("platform")
        platform_count = None
        if platform_val and str(platform_val).isdigit():
            platform_count = int(platform_val)
            
        # Fallback to degree + jitter
        if platform_count is None:
            # Graph degree (e.g., degree 2 = continuous single track = 1 platform approx, degree 4 = 2 tracks crossing = 2 platforms approx)
            degree = router.G.degree(node_id)
            base_platforms = max(1, degree // 2)
            # Add small random jitter (0 to 1) to make it look realistic if degree is low
            jitter = random.randint(0, 1)
            platform_count = base_platforms + jitter
            
        if node_id not in registry:
            registry[node_id] = {
                "name": str(name),
                "lat": lat,
                "lon": lon,
                "platform_count": platform_count,
                "track_profile": None,
                "train_profile": None
            }
        else:
            # We already snapped a station here. Keep the one with actual platform count if possible
            if platform_count > registry[node_id]["platform_count"] and platform_val is not None:
                registry[node_id]["platform_count"] = platform_count

    logger.info(f"Built registry with {len(registry)} unique station nodes.")
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
        
    logger.info(f"Saved station registry to {out_file}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", type=str, default="uk")
    args = parser.parse_args()
    build_station_registry(args.country)
