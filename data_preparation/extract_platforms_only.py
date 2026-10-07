import sys
from pathlib import Path
from pyrosm import OSM
import geopandas as gpd

def save_geojson(gdf, output_path):
    for col in gdf.columns:
        if col == "geometry": continue
        if gdf[col].dtype == "object":
            gdf[col] = gdf[col].apply(lambda x: str(x) if isinstance(x, (list, dict, set)) else x)
    gdf.to_file(str(output_path), driver="GeoJSON")
    print(f"Saved {len(gdf)} platforms to {output_path}")

def main():
    pbf_path = Path("data/raw/uk/great-britain-latest.osm.pbf")
    if not pbf_path.exists():
        print("PBF file not found!")
        sys.exit(1)
        
    print(f"Loading OSM from {pbf_path}...")
    osm = OSM(str(pbf_path))
    
    print("Extracting platforms...")
    platforms_gdf = osm.get_data_by_custom_criteria(
        custom_filter={"railway": ["platform"], "public_transport": ["platform"]},
        filter_type="keep",
        keep_nodes=True,
        keep_ways=True,
        keep_relations=True,
        extra_attributes=["name", "ref"]
    )
    
    if platforms_gdf is not None and not platforms_gdf.empty:
        output_path = Path("data/processed/geojson/uk/uk_platforms.geojson")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_geojson(platforms_gdf, output_path)
    else:
        print("No platforms found.")

if __name__ == "__main__":
    main()
