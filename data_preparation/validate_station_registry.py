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
        
    # Check max platform
    max_plat = max(counts)
    print(f"Max platform count: {max_plat}")
    if max_plat < 15:
        print(f"FAIL: Max platform count {max_plat} is < 15")
        sys.exit(1)
        
    # Distribution
    print("\nPlatform Source Distribution:")
    for source, count in sources.items():
        pct = (count / len(registry)) * 100
        print(f"  {source}: {count} ({pct:.1f}%)")
        
    # Check default %
    default_pct = (sources.get("default", 0) / len(registry)) * 100
    print(f"Percentage on 'default': {default_pct:.1f}%")
    
    # Check duplicate display names
    dup_names = [name for name, count in Counter(display_names).items() if count > 1]
    if dup_names:
        print(f"FAIL: Found {len(dup_names)} duplicate display names. Examples: {dup_names[:5]}")
        sys.exit(1)
    else:
        print("SUCCESS: 0 duplicate display names.")
        
    # Anchor checks
    anchors = {
        "London King's Cross": (10, 14),
        "Edinburgh Waverley": (16, 22),
        "Reading": (12, 17),
        "Crewe": (10, 14),
        "Birmingham New Street": (10, 13),
        "Clapham Junction": (15, 18),
        "Manchester Piccadilly": (12, 15)
    }
    
    anchor_fails = []
    for anchor, (min_c, max_c) in anchors.items():
        found = False
        for data in registry.values():
            if data.get("name") == anchor:
                found = True
                c = data.get("platform_count", 0)
                if not (min_c <= c <= max_c):
                    anchor_fails.append(f"{anchor} has {c} platforms, expected {min_c}-{max_c}")
                break
        if not found:
            anchor_fails.append(f"{anchor} not found in registry")
            
    if anchor_fails:
        print("FAIL: Anchor checks failed:")
        for fail in anchor_fails:
            print(f"  {fail}")
        sys.exit(1)
    else:
        print("SUCCESS: All anchor checks passed.")
        
    print("\nValidation passed successfully!")

if __name__ == "__main__":
    validate_registry()
