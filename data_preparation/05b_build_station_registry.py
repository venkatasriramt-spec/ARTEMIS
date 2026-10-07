import os
import json
import logging
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point
import pandas as pd
import importlib.util
import re
from collections import Counter

# Load core_engine/06_spatial_routing.py
spec = importlib.util.spec_from_file_location("spatial_routing", str(Path(__file__).resolve().parent.parent / "core_engine" / "06_spatial_routing.py"))
spatial_routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spatial_routing)
RailwayRouter = spatial_routing.RailwayRouter
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def normalize_name(name):
    if not name or pd.isna(name): return ""
    name = str(name).lower()
    name = re.sub(r'\brailway station\b', '', name)
    name = re.sub(r'\bstation\b', '', name)
    return name.strip()

def parse_platforms_tag(tag_val):
    if not tag_val or pd.isna(tag_val):
        return None
    val = str(tag_val).strip()
    if val.isdigit():
        return int(val)
    # Check for simple ranges e.g. "1-4"
    m = re.match(r'^(\d+)-(\d+)$', val)
    if m:
        return int(m.group(2)) - int(m.group(1)) + 1
    # Check for semicolon delimited "1;2;3"
    if ';' in val:
        return len([x for x in val.split(';') if x.strip()])
    return None

def build_station_registry(country_code="uk"):
    base_dir = Path(__file__).resolve().parent.parent
    geojson_dir = base_dir / "data" / "processed" / "geojson" / country_code
    stations_file = geojson_dir / f"{country_code}_stations.geojson"
    platforms_file = geojson_dir / f"{country_code}_platforms.geojson"
    out_file = geojson_dir / "stations.json"
    reference_file = base_dir / "data" / "reference" / "uk_major_station_platforms.json"

    if not stations_file.exists():
        logger.error(f"Stations file not found: {stations_file}")
        return

    # Load Curated
    curated = {}
    if reference_file.exists():
        with open(reference_file, "r") as f:
            cdata = json.load(f)
            for k, v in cdata.items():
                curated[normalize_name(k)] = v["count"]

    logger.info(f"Loading {country_code} stations from {stations_file}...")
    stations_gdf = gpd.read_file(stations_file)
    
    # Filter Stations
    initial_count = len(stations_gdf)
    valid_stations = []
    dropped_reasons = Counter()
    
    for idx, row in stations_gdf.iterrows():
        is_valid = True
        row_dict = row.to_dict()
        
        station_val = str(row_dict.get('station', '')).lower()
        if station_val in ['subway', 'light_rail', 'tram']:
            dropped_reasons['metro_light_rail'] += 1
            is_valid = False
            continue
            
        for k, v in row_dict.items():
            k_str, v_str = str(k).lower(), str(v).lower()
            if 'preserved' in k_str or 'preserved' in v_str:
                dropped_reasons['preserved'] += 1
                is_valid = False
                break
            if 'disused' in k_str or 'disused' in v_str:
                dropped_reasons['disused'] += 1
                is_valid = False
                break
            if 'abandoned' in k_str or 'abandoned' in v_str:
                dropped_reasons['abandoned'] += 1
                is_valid = False
                break
            if 'construction' in k_str or 'construction' in v_str:
                dropped_reasons['construction'] += 1
                is_valid = False
                break
                
        if not is_valid:
            continue
            
        name = row_dict.get('name')
        if not name or pd.isna(name):
            dropped_reasons['no_name'] += 1
            continue
            
        valid_stations.append(row)
        
    stations_gdf = gpd.GeoDataFrame(valid_stations, crs=stations_gdf.crs)
    logger.info(f"Filtered stations from {initial_count} to {len(stations_gdf)}")
    for r, c in dropped_reasons.items():
        logger.info(f"  Dropped {c} due to {r}")
        
    stations_metric = stations_gdf.to_crs("EPSG:27700")
    
    platforms_metric = None
    if platforms_file.exists():
        platforms_gdf = gpd.read_file(platforms_file)
        if not platforms_gdf.empty:
            platforms_metric = platforms_gdf.to_crs("EPSG:27700")
            
    # Cluster stations
    logger.info("Clustering stations...")
    clusters = []
    assigned = set()
    for idx, row in stations_metric.iterrows():
        if idx in assigned: continue
        norm_name = normalize_name(row['name'])
        
        close_mask = stations_metric.geometry.distance(row.geometry) <= 500
        name_mask = stations_metric['name'].apply(normalize_name) == norm_name
        cluster_idx = stations_metric[close_mask & name_mask].index.tolist()
        
        assigned.update(cluster_idx)
        clusters.append(cluster_idx)
        
    logger.info(f"Clustered into {len(clusters)} distinct stations")
    
    logger.info("Initializing RailwayRouter to snap stations to nodes...")
    router = RailwayRouter(country_code)
    
    registry = {}
    
    for cluster in clusters:
        cluster_rows = stations_gdf.loc[cluster]
        cluster_metric_rows = stations_metric.loc[cluster]
        
        centroid_metric = cluster_metric_rows.geometry.unary_union.centroid
        centroid_wgs84 = gpd.GeoSeries([centroid_metric], crs="EPSG:27700").to_crs("EPSG:4326").iloc[0]
        
        # Best name (longest)
        best_name = cluster_rows.loc[cluster_rows['name'].str.len().idxmax(), 'name']
        norm_name = normalize_name(best_name)
        
        # OSM ID
        primary_id = cluster_rows.loc[cluster_rows['name'].str.len().idxmax(), 'id']
        
        platform_count = None
        platform_source = None
        
        # 1. Curated
        if norm_name in curated:
            platform_count = curated[norm_name]
            platform_source = "curated"
            
        # 2. OSM Platform Features
        if platform_count is None and platforms_metric is not None:
            close_platforms = platforms_metric[platforms_metric.geometry.distance(centroid_metric) <= 250]
            if not close_platforms.empty:
                refs = []
                for _, p_row in close_platforms.iterrows():
                    ref = p_row.get('ref')
                    if ref and not pd.isna(ref):
                        for r in str(ref).split(';'):
                            if r.strip():
                                refs.append(r.strip())
                
                distinct_refs = set(refs)
                if len(distinct_refs) > 0:
                    platform_count = len(distinct_refs)
                    platform_source = "osm_platform_features"
                else:
                    buffered = close_platforms.geometry.buffer(5)
                    merged = buffered.unary_union
                    if hasattr(merged, 'geoms'):
                        count = len(merged.geoms)
                    else:
                        count = 1
                    if count >= 1:
                        platform_count = count
                        platform_source = "osm_platform_features"
                        
        # 3. OSM Tag platforms
        if platform_count is None:
            for _, r in cluster_rows.iterrows():
                if 'platforms' in r:
                    p_tag = r['platforms']
                    c = parse_platforms_tag(p_tag)
                    if c is not None and c > 0:
                        platform_count = c
                        platform_source = "osm_tag"
                        break
                        
        # 4. Default
        if platform_count is None:
            platform_count = 2
            platform_source = "default"
            
        # Snap to Graph
        try:
            node_id, node_data, dist_km = router.find_nearest_node(centroid_wgs84.y, centroid_wgs84.x)
        except Exception as e:
            logger.warning(f"Could not snap station {best_name}: {e}")
            continue
            
        if dist_km > 5.0:
            dropped_reasons['too_far_from_tracks'] += 1
            continue
            
        registry[str(primary_id)] = {
            "name": str(best_name),
            "display_name": str(best_name),
            "lat": centroid_wgs84.y,
            "lon": centroid_wgs84.x,
            "platform_count": platform_count,
            "platform_source": platform_source,
            "graph_node_id": node_id,
            "track_profile": None,
            "train_profile": None
        }
        
    # Disambiguate duplicate names
    name_counts = Counter(d['name'] for d in registry.values())
    for d in registry.values():
        if name_counts[d['name']] > 1:
            d['display_name'] = f"{d['name']} ({d['lat']:.2f},{d['lon']:.2f})"
            
    logger.info(f"Final registry size: {len(registry)}")
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
        
    logger.info(f"Saved station registry to {out_file}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", type=str, default="uk")
    args = parser.parse_args()
    build_station_registry(args.country)
