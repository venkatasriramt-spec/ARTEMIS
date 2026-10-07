import json
import sys
from pathlib import Path
from collections import Counter

def validate_registry():
    registry_path = Path("data/processed/geojson/uk/stations.json")
    if not registry_path.exists():
        print(f"Registry not found: {registry_path}")
        sys.exit(1)
        
    with open(registry_path, "r") as f:
        registry = json.load(f)
        
    if not registry:
        print("Registry is empty")
        sys.exit(1)
        
    print(f"Total stations in registry: {len(registry)}")
    
    counts = []
    sources = Counter()
    display_names = []
    
    for station_id, data in registry.items():
        counts.append(data.get("platform_count", 0))
        sources[data.get("platform_source", "default")] += 1
        display_names.append(data.get("display_name", data.get("name")))
        
    # Check max platform for non-curated
    max_plat = 0
    for data in registry.values():
        c = data.get("platform_count", 0)
        s = data.get("platform_source", "default")
        if s != "curated" and c > max_plat:
            max_plat = c
        if s != "curated" and c > 24:
            print(f"FAIL: Non-curated station {data['name']} has >24 platforms ({c})")
            sys.exit(1)
            
    print(f"Max non-curated platform count: {max_plat}")
        
    # Distribution
    print("\nPlatform Source Distribution:")
    for source, count in sources.items():
        pct = (count / len(registry)) * 100
        print(f"  {source}: {count} ({pct:.1f}%)")
        
    # Check default %
    default_pct = (sources.get("default", 0) / len(registry)) * 100
    print(f"Percentage on 'default': {default_pct:.1f}%")
    if default_pct > 10.0:
        print(f"FAIL: default percentage {default_pct:.1f}% is > 10%")
        sys.exit(1)
    
    # Check duplicate display names
    dup_names = [name for name, count in Counter(display_names).items() if count > 1]
    if dup_names:
        print(f"FAIL: Found {len(dup_names)} duplicate display names. Examples: {dup_names[:5]}")
        sys.exit(1)
    else:
        print("SUCCESS: 0 duplicate display names.")
        
    # Tag-based check
    import geopandas as gpd
    raw_stations = gpd.read_file('data/processed/geojson/uk/uk_stations.geojson')
    
    surviving_invalid = []
    for key, d in registry.items():
        # Find the row in raw_stations
        row = raw_stations[raw_stations['id'] == int(key)]
        if not row.empty:
            row = row.iloc[0]
            is_invalid = False
            for tag in ['subway', 'light_rail', 'tram', 'preserved', 'miniature']:
                if str(row.get(tag, '')).lower() in ['yes', 'true', '1']:
                    is_invalid = True
                    break
            if str(row.get('station', '')).lower() in ['subway', 'light_rail', 'tram', 'miniature']:
                is_invalid = True
                
            if is_invalid:
                if 'national rail' not in str(row.get('network', '')).lower():
                    surviving_invalid.append(d['name'])
                    
    if surviving_invalid:
        print(f"FAIL: Found {len(surviving_invalid)} stations with forbidden tags in registry. Examples: {list(set(surviving_invalid))[:5]}")
        sys.exit(1)
            
    # Holdout checks
    holdout_path = Path("config/reference/uk_validation_holdout.json")
    if holdout_path.exists():
        with open(holdout_path, "r") as f:
            holdouts = json.load(f)
            
        print("\nHold-out Comparison (Expected vs Actual):")
        holdout_fails = []
        for h_name, h_data in holdouts.items():
            expected = h_data["count"]
            found = False
            for data in registry.values():
                if data.get("name") == h_name:
                    found = True
                    actual = data.get("platform_count", 0)
                    print(f"  {h_name}: Expected {expected}, Actual {actual} ({data.get('platform_source')})")
                    if abs(actual - expected) > 1:
                        holdout_fails.append(f"{h_name}: Expected {expected}, got {actual}")
                    break
            if not found:
                holdout_fails.append(f"{h_name} not found in registry")
                
        if holdout_fails:
            print("FAIL: Holdout checks failed:")
            for fail in holdout_fails:
                print(f"  {fail}")
            sys.exit(1)
        else:
            print("SUCCESS: All holdout checks passed.")
            
    # Top 20 stations
    print("\nTop 20 stations by platform count:")
    sorted_stations = sorted(registry.values(), key=lambda x: x.get("platform_count", 0), reverse=True)
    for i, data in enumerate(sorted_stations[:20]):
        print(f"  {i+1}. {data['name']}: {data['platform_count']} ({data['platform_source']})")
        
    print("\nValidation passed successfully!")

if __name__ == "__main__":
    validate_registry()
