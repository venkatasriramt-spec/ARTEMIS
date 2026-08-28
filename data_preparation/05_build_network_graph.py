import os
import json
import logging
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import networkx as nx
import geopandas as gpd
from google.cloud import storage
from shapely.geometry import LineString, MultiLineString
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def build_graph_for_country(country_code, geojson_dir, output_dir):
    """
    Reads the GeoJSON tracks and stations for a country and builds a NetworkX 
    routing graph. Saves the output as a GraphML file.
    """
    tracks_file = os.path.join(geojson_dir, country_code, f"{country_code}_tracks.geojson")
    stations_file = os.path.join(geojson_dir, country_code, f"{country_code}_stations.geojson")
    
    if not os.path.exists(tracks_file):
        logger.warning(f"[{country_code}] Local {country_code}_tracks.geojson not found. Please ensure it was downloaded from GCS.")
        return f"Skipped {country_code} (no tracks.geojson found)"

    logger.debug(f"[{country_code}] Loading GeoJSON...")
    try:
        # Load the tracks
        gdf_tracks = gpd.read_file(tracks_file)
        
        # Load stations if they exist
        gdf_stations = None
        if os.path.exists(stations_file):
            gdf_stations = gpd.read_file(stations_file)

        # Initialize Directed Graph
        G = nx.DiGraph(name=f"{country_code}_railway_network")
        
        # Iterate over all tracks to build edges and nodes using fast itertuples
        for row in gdf_tracks.itertuples(index=False):
            geom = getattr(row, "geometry", None)
            if isinstance(geom, LineString):
                lines = [geom]
            elif isinstance(geom, MultiLineString):
                lines = list(geom.geoms)
            else:
                continue
                
            # Attributes to attach to the edge
            edge_attrs = {
                "osm_id": str(getattr(row, "id", "")),
                "name": str(getattr(row, "name", "")),
                "railway": str(getattr(row, "railway", "")),
                "maxspeed": str(getattr(row, "maxspeed", "")),
                "gauge": str(getattr(row, "gauge", "")),
                "electrified": str(getattr(row, "electrified", ""))
            }
            
            for line in lines:
                coords = list(line.coords)
                if len(coords) < 2:
                    continue
                    
                for i in range(len(coords) - 1):
                    # We use exactly the string representation of coordinates without rounding
                    # to ensure exact matching of nodes across different ways.
                    u = f"{coords[i][0]},{coords[i][1]}"
                    v = f"{coords[i+1][0]},{coords[i+1][1]}"
                    
                    # Add nodes (with positions)
                    G.add_node(u, x=coords[i][0], y=coords[i][1])
                    G.add_node(v, x=coords[i+1][0], y=coords[i+1][1])
                    
                    # Add edge
                    G.add_edge(u, v, **edge_attrs)
                    # If bidirectional track, add reverse edge (simplification)
                    G.add_edge(v, u, **edge_attrs)

        # Output dir
        country_out = os.path.join(output_dir, country_code)
        os.makedirs(country_out, exist_ok=True)
        
        # Save as GraphML
        out_file = os.path.join(country_out, f"{country_code}_network.graphml")
        nx.write_graphml(G, out_file)
        
        return f"✓ Built graph for {country_code}: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges"
        
    except Exception as e:
        logger.error(f"[{country_code}] Failed: {str(e)}")
        return f"✗ Failed {country_code}: {str(e)}"

def download_from_gcs(bucket_name, country_code, geojson_dir):
    """Downloads the GeoJSON files for a country from GCS."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    
    country_dir = os.path.join(geojson_dir, country_code)
    os.makedirs(country_dir, exist_ok=True)
    
    files_downloaded = 0
    for filename in [f"{country_code}_tracks.geojson", f"{country_code}_stations.geojson"]:
        blob_name = f"processed/geojson/{country_code}/{filename}"
        blob = bucket.blob(blob_name)
        
        local_path = os.path.join(country_dir, filename)
        if not os.path.exists(local_path):
            if blob.exists():
                blob.download_to_filename(local_path)
                files_downloaded += 1
                
    return files_downloaded

def main():
    parser = argparse.ArgumentParser(description="Build Network Graphs from GeoJSON")
    parser.add_argument("--bucket", type=str, default="artemis-railway-data", help="GCS Bucket name to download from")
    args = parser.parse_args()

    print("=" * 60)
    print("ARTEMIS - Phase 2: Build Network Graph")
    print(f"Bucket: gs://{args.bucket}")
    print("=" * 60)
    
    # Paths
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_file = os.path.join(base_dir, "config", "countries.json")
    geojson_dir = os.path.join(base_dir, "data", "processed", "geojson")
    output_dir = os.path.join(base_dir, "data", "processed", "graph")
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Load config
    with open(config_file, 'r') as f:
        config = json.load(f)
    countries = [c["code"] for c in config.get("countries", [])]
    
    # 1. Download missing GeoJSON files from GCS
    print(f"\nChecking for missing GeoJSON files from gs://{args.bucket}...")
    for country in countries:
        try:
            downloaded = download_from_gcs(args.bucket, country, geojson_dir)
            if downloaded > 0:
                print(f"  Downloaded {downloaded} files for {country}")
        except Exception as e:
            print(f"  Warning: Could not check/download files for {country}: {e}")

    # 2. Build Graphs
    print(f"\nBuilding graphs for {len(countries)} countries using {os.cpu_count()} workers...")
    
    results = []
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = {
            executor.submit(build_graph_for_country, country, geojson_dir, output_dir): country
            for country in countries
        }
        
        for future in tqdm(as_completed(futures), total=len(countries), desc="Building Graphs"):
            results.append(future.result())
            
    print("\nResults:")
    for r in sorted(results):
        print(f"  {r}")
        
    print(f"\nGraphs saved to: {output_dir}")

if __name__ == "__main__":
    main()
