# ARTEMIS — Autonomous Railway Throughput & Management Intelligent System

## Railway Network Intelligence Platform

Extract, route, and visualize railway networks across 14 countries using OpenStreetMap data, spatial indexing, and graph-based pathfinding algorithms — all running on Google Cloud.

---

## 🗂 Project Structure

```
ARTEMIS/
├── config/
│   └── countries.json              # Country metadata & download URLs
├── data_preparation/               # Scripts to process map data and build graph
│   ├── 01_download_pbf.py
│   ├── 02_extract_railway.py
│   ├── ...
│   └── pipeline.py
├── core_engine/                    # A* pathfinding and visualization engine
│   ├── 06_spatial_routing.py
│   ├── 07_visualization_server.py
│   └── 08_simulation_engine.py
├── versions/
│   └── v1/                         # Centralized PPO RL Architecture
│       ├── scripts/
│       │   ├── train_env.py
│       │   └── train_ppo.py
│       ├── models/
│       │   └── ppo_artemis_uk_final.zip
│       └── logs/
│           └── ppo_artemis_tensorboard/
├── infra/
├── data/                           # Generated data (not committed)
├── requirements.txt
└── README.md
```

## 🌍 Supported Countries

| # | Country | Code | Geofabrik Source | Est. PBF Size |
|---|---------|------|-----------------|---------------|
| 1 | United States | `us` | north-america/us | ~10 GB |
| 2 | China | `china` | asia/china | ~1.5 GB |
| 3 | Russia | `russia` | russia | ~3.5 GB |
| 4 | Canada | `canada` | north-america/canada | ~3.0 GB |
| 5 | Germany | `germany` | europe/germany | ~4.0 GB |
| 6 | Australia | `australia` | australia-oceania/australia | ~1.2 GB |
| 7 | Argentina | `argentina` | south-america/argentina | ~0.5 GB |
| 8 | Brazil | `brazil` | south-america/brazil | ~1.8 GB |
| 9 | France | `france` | europe/france | ~4.5 GB |
| 10 | Japan | `japan` | asia/japan | ~2.0 GB |
| 11 | South Africa | `south_africa` | africa/south-africa | ~0.3 GB |
| 12 | Mexico | `mexico` | north-america/mexico | ~0.7 GB |
| 13 | United Kingdom | `uk` | europe/great-britain | ~1.5 GB |
| 14 | Italy | `italy` | europe/italy | ~2.0 GB |

---

## 🚀 Quick Start

### 1. Local Setup (Development)

```bash
cd ARTEMIS
python -m venv venv
source venv/bin/activate      # Linux/Mac
# venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

### 2. Run the Full Pipeline (Phase 1)

```bash
# Full pipeline: Download → Extract → Convert → Upload
python data_preparation/pipeline.py

# Process specific countries only
python data_preparation/pipeline.py --countries uk italy germany

# Run specific steps
python data_preparation/pipeline.py --steps download extract
```

### 3. Build Network Graphs (Phase 2)

```bash
# Builds GraphML routing graphs for all 14 countries
python data_preparation/05_build_network_graph.py
```

### 4. Run the Routing & Visualization Server (Phase 3)

```bash
# Install Phase 3 dependencies (if not already in VM startup script)
pip install scipy fastapi uvicorn

# Calculate a route from the CLI
python core_engine/06_spatial_routing.py uk --start "51.5074,-0.1278" --end "55.9533,-3.1883"

# Start the interactive web map
python core_engine/07_visualization_server.py
# Access at http://localhost:8000 (or via Vertex AI proxy at /proxy/8000/)
```

### 5. Backup Generated Graphs to GCS

```bash
python data_preparation/backup_graphs.py
```

---

## ☁️ Google Cloud Setup

### Vertex AI Notebook Instance

```bash
# Provision the Notebook Instance
chmod +x infra/vm_setup.sh
./infra/vm_setup.sh

# Or with explicit project override
./infra/vm_setup.sh --project fine-ring-505908-e6
```

**Current Notebook Specifications:**

| Setting | Value | Rationale |
|---------|-------|-----------|
| Machine Type | `n2d-highmem-16` | 16 vCPUs, 128 GB RAM for graph processing & multi-country routing |
| Boot Disk | 200 GB SSD (`PD_SSD`) | Fits GCP SSD quota (500 GB limit in `us-central1`) |
| OS/Environment | Google Deep Learning VM | Pre-installed JupyterLab, Git, conda, Python 3 |
| Region | `us-central1` | Low-cost, good connectivity |

### GCS Bucket

```bash
chmod +x infra/gcs_setup.sh
./infra/gcs_setup.sh
```

### Deploy to Vertex AI Notebook

```bash
# Copy project files into the Jupyter workspace
gcloud compute scp --recurse \
    ./scripts ./config ./requirements.txt \
    artemis-notebook:/home/jupyter/artemis/ \
    --zone=us-central1-c

# Access JupyterLab via Google Cloud Console:
#   Vertex AI > Workbench > Instances > Click "OPEN JUPYTERLAB"
```

---

## 📊 Pipeline Architecture

```
Phase 1: Data Acquisition          Phase 2: Graph Build         Phase 3: Routing & Visualization
┌──────────────────────────┐    ┌─────────────────────┐    ┌────────────────────────────────┐
│ 01_download_pbf.py       │    │ 05_build_network_    │    │ 06_spatial_routing.py          │
│   Geofabrik → .osm.pbf   │ →  │   graph.py           │ →  │   KD-Tree + A* Pathfinding     │
│                          │    │   GeoJSON → GraphML  │    │                                │
│ 02_extract_railway.py    │    │   ProcessPoolExec    │    │ 07_visualization_server.py     │
│   osmium → pyrosm → GeoJ│    │   (16 cores)         │    │   FastAPI + Leaflet Map        │
│                          │    │                     │    │   Station-to-Station Routing   │
│ 03_convert_to_kml.py     │    │                     │    │   Inter-Country Graph Merging  │
│   GeoJSON → Styled KML   │    └─────────────────────┘    │                                │
│                          │              ↕                │ backup_graphs.py               │
│ 04_upload_to_gcs.py      │    ┌─────────────────────┐    │   GraphML → GCS Persistence    │
│   All files → GCS Bucket │    │ GCS Bucket           │    └────────────────────────────────┘
└──────────────────────────┘    │ gs://artemis-railway │
                                │   -data              │
                                └─────────────────────┘
```

**Phase 1 Data Flow:**
`.osm.pbf` (Geofabrik) → `.geojson` (categorized) → `.kml` (styled) → GCS Bucket

**Phase 2 Data Flow:**
`.geojson` (from GCS) → `NetworkX DiGraph` → `.graphml` → GCS Bucket

**Phase 3 Data Flow:**
`.graphml` → `KD-Tree Index` → `A* Routing` → `Leaflet Map Visualization`

---

## 🔧 Configuration

Edit `config/countries.json` to:
- Add/remove countries
- Change Geofabrik URLs
- Modify railway types to extract
- Update GCS bucket settings

### Railway Types Extracted (Intercity/Long-Distance Only)

| Category | Types | Description |
|----------|-------|-------------|
| Tracks | `rail`, `narrow_gauge` | Long-distance/intercity railway lines |
| Stations | `station`, `halt` | Stops and stations |
| Infrastructure | `signal`, `switch`, `crossing` | Track infrastructure |
| Service | `spur`, `siding`, `yard` | Service tracks |

> **Note:** Subway, tram, light rail, and monorail are excluded — only intercity/interstate trains are extracted.

---

## 📝 Key Technical Details

### Phase 2: Graph Construction
- **MultiLineString Handling:** GeoJSON tracks may contain `MultiLineString` geometries (merged OSM ways). The builder explodes these into individual `LineString` segments to preserve connectivity.
- **Node IDs:** Node IDs are the full-precision string representation of their `(lon,lat)` coordinates. This ensures that overlapping track endpoints from different OSM ways are automatically merged into the same graph node.
- **Parallelism:** 14 countries are built concurrently using `ProcessPoolExecutor` with 16 workers.

### Phase 3: Spatial Routing
- **KD-Tree:** `scipy.spatial.cKDTree` provides O(log n) nearest-neighbor lookups for snapping GPS coordinates to railway nodes.
- **Connected Component:** The Largest Strongly Connected Component is extracted to remove disconnected spurs and guarantee routability.
- **Haversine Distance:** All edge weights and the A* heuristic use the Haversine formula for accurate great-circle distance on the Earth's surface.
- **Inter-Country Routing:** When start and end countries differ, the backend dynamically merges both GraphML files using `nx.compose()`, building a unified cross-border routing graph.
- **Station Routing:** The `/api/stations` endpoint parses `[country]_stations.geojson` files and serves named stations as searchable dropdowns. Results are cached in-memory.

---

## ⚠️ Important Notes

1. **Overpass API limitations**: The Overpass API will timeout for large countries (US, Russia, China). Always use the Geofabrik pipeline for these.
2. **Disk space**: Full pipeline needs ~200 GB for all 14 countries' PBF files.
3. **Memory Optimization**: The pipeline uses `osmium-tool` for pre-filtering and a custom streaming KML writer, keeping RAM usage extremely low even for massive datasets (like the 10 GB US `.pbf`).
4. **GCP Quotas**: The `us-central1` region has a 16 N2D vCPU quota and a 500 GB SSD quota. The VM is configured within these limits.
5. **GCP credentials**: Set up `GOOGLE_APPLICATION_CREDENTIALS` or run `gcloud auth application-default login` before uploading.

---

## 📄 License

This project uses OpenStreetMap data, which is © OpenStreetMap contributors and available under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).
