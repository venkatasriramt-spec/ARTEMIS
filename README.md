# ARTEMIS — Autonomous Railway Throughput & Management Intelligent System

## Railway Network Intelligence Platform

Extract, route, and visualize railway networks across 14 countries using OpenStreetMap data, spatial indexing, graph-based pathfinding, and **Reinforcement Learning** — all running locally or on Google Cloud.

---

## 🗂 Project Structure

```
ARTEMIS/
├── config/
│   ├── countries.json                  # Country metadata & download URLs
│   └── reference/                      # Curated overrides and hold-out sets
├── data_preparation/                   # Scripts to process map data and build graphs
│   ├── 01_download_pbf.py
│   ├── 02_extract_railway.py
│   ├── 03_convert_to_kml.py
│   ├── 04_upload_to_gcs.py
│   ├── 05_build_network_graph.py
│   ├── 05b_build_station_registry.py    # Build stations.json registry from GeoJSON
│   ├── extract_platforms_only.py        # Extracts OSM platform geometries for counting
│   ├── validate_station_registry.py     # Validates station platform counts against anchors
│   ├── backup_graphs.py
│   ├── download_from_gcs.py
│   ├── overpass_fallback.py
│   └── pipeline.py
├── core_engine/                        # Routing, Visualization & Simulation
│   ├── 06_spatial_routing.py           # KD-Tree + A* pathfinding engine
│   ├── 07_visualization_server.py      # FastAPI server + Interactive dashboard
│   └── 08_simulation_engine.py         # Legacy discrete-event simulation
├── versions/
│   ├── v1/                             # RL v1: Centralized PPO Architecture
│   │   ├── scripts/
│   │   │   ├── train_env.py
│   │   │   └── train_ppo.py
│   │   └── models/
│   │       ├── ppo_artemis_uk_final.zip
│   │       └── ppo_artemis_uk_weights/   # Clean .pth weights for GitHub
│   └── v2/                             # RL v2: Decentralized Shared-Radar PPO (Active)
│       ├── scripts/
│       │   ├── train_env_v2.py         # Gymnasium env with dynamic station injection
│       │   ├── train_ppo_v2.py         # Training script with FlattenMultiAgentVecEnv
│       │   ├── rl_sim_controller.py    # Real-time simulation bridge for the dashboard
│       │   └── reexport_weights.py     # Extracts clean .pth files from SB3 zips
│       ├── models/
│       │   ├── ppo_artemis_uk_final.zip
│       │   └── ppo_artemis_uk_weights/   # Clean .pth weights for GitHub
│       └── logs/
│           └── ppo_artemis_tensorboard/
├── infra/                              # GCP provisioning scripts
│   ├── gcs_setup.sh                    # GCS bucket provisioning
│   ├── vm_setup.sh                     # Compute Engine VM provisioning
│   └── desktop_virtualization_setup.sh # XFCE + XRDP desktop setup
├── data/                               # Generated data (not committed)
├── docs/
│   ├── project_history.md              # Full chronological dev log
│   └── software_requirements_document.md
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

### 1. Local Setup

```bash
cd ARTEMIS

# Install system dependencies (Ubuntu/Debian)
sudo apt-get install -y python3-pip build-essential python3.10-dev

# Install Python dependencies globally
pip3 install --user -r requirements.txt
```

> **Note:** A virtual environment is not required. The project runs with globally installed packages.

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

### 4. Run the RL Simulation Dashboard (Phase 5)

```bash
# Set your Google Maps API key (or add it to a .env file)
export GOOGLE_MAPS_API_KEY="your_api_key_here"

# (Optional) Set the target country code (default is "uk")
export ARTEMIS_COUNTRY="uk"

# Start the interactive web dashboard
python core_engine/07_visualization_server.py
# Access at http://127.0.0.1:8000/
```

**Using the Dashboard:**
1. Wait for the station list to load (hundreds of real UK stations).
2. Select a **Start Station** and **End Station** from the dropdowns.
3. Click **Deploy Train** to queue the route. Repeat for as many trains as you want. The simulation starts automatically when the first train is deployed.
4. You can continue deploying new trains into a running simulation.
5. Watch the RL agent navigate all trains in real-time on the map.

### 5. Train a New RL Model (Optional)

```bash
# Train a PPO model on the UK network
python versions/v2/scripts/train_ppo_v2.py --country uk
```

### 6. Re-export Weights for Distribution

```bash
# Extract clean .pth files from SB3 .zip files to avoid AV false positives
python versions/v2/scripts/reexport_weights.py
```

### 7. Backup Generated Graphs to GCS

```bash
python data_preparation/backup_graphs.py
```

---

## 📊 Pipeline Architecture

```
Phase 1: Data Acquisition       Phase 2: Graph Build        Phase 3: Routing Engine
┌────────────────────────┐   ┌─────────────────────┐   ┌──────────────────────────┐
│ 01_download_pbf.py     │   │ 05_build_network_    │   │ 06_spatial_routing.py    │
│   Geofabrik → .osm.pbf │ → │   graph.py           │ → │   KD-Tree + A*           │
│                        │   │   GeoJSON → GraphML  │   │   Pathfinding            │
│ 02_extract_railway.py  │   │   ProcessPoolExec    │   └──────────────────────────┘
│   pyrosm (direct)      │   │   (16 cores)         │              ↓
│                        │   └─────────────────────┘   ┌──────────────────────────┐
│ 03_convert_to_kml.py   │                             │ Phase 4: RL Training     │
│   GeoJSON → Styled KML │                             │ v1: Centralized PPO      │
│                        │                             │ v2: Decentralized Radar  │
│ 04_upload_to_gcs.py    │                             │   (Shared Policy)        │
│   All files → GCS      │                             └──────────────────────────┘
└────────────────────────┘                                         ↓
                                                       ┌──────────────────────────┐
                                                       │ Phase 5: Interactive     │
                                                       │ Simulation Dashboard     │
                                                       │ 07_visualization_server  │
                                                       │   FastAPI + Google Maps  │
                                                       │   Station Selection UI   │
                                                       │   Real-time RL Sim       │
                                                       └──────────────────────────┘
```

---

## 🤖 Reinforcement Learning Architecture

### v1: Centralized PPO (Deprecated)
- **Observation:** Flattened state of all trains simultaneously `(num_agents * features)`.
- **Action Space:** `MultiDiscrete([3] * num_agents)` — one action per train per step.
- **Limitation:** Model is tightly coupled to a fixed number of agents. Cannot scale dynamically.

### v2: Decentralized Shared-Radar PPO (Active)
- **Observation:** Local radar per train `(3,)` — `[current_speed, edge_speed_limit, dist_to_nearest_train]`.
- **Action Space:** `Discrete(3)` — Brake / Maintain / Accelerate.
- **Key Innovation:** A custom `FlattenMultiAgentVecEnv` wrapper "unwraps" the multi-agent environment so SB3 sees each train as an independent single-agent env. This trains a **single shared policy** that is applied to every train independently.
- **Scaling:** Because each train uses the same 3-feature radar, the model works with **any number of trains** at inference time without retraining.
- **Distribution:** To avoid antivirus false positives on GitHub caused by the Python pickle data inside Stable-Baselines3 `.zip` files, we use `reexport_weights.py` to extract and distribute clean `.pth` weights in the `_weights/` directories.

---

## ☁️ Google Cloud Setup

### Desktop Virtualization

ARTEMIS was developed on a **GCP Virtual Desktop** (XFCE + XRDP on a Compute Engine instance). The underlying CPU instance type was changed as needed depending on the workload — scaled up for heavy graph processing and RL training, and scaled down during lighter development work.

```bash
# Provision a full XFCE virtual desktop with Python & geospatial dependencies
chmod +x infra/desktop_virtualization_setup.sh
./infra/desktop_virtualization_setup.sh
```

| Setting | Value | Rationale |
|---------|-------|-----------|
| Machine Type | Adjusted as needed (e.g., `n2d-highmem-16`) | Scaled up/down for graph processing, RL training, or light development |
| Boot Disk | 200 GB SSD (`PD_SSD`) | Fits GCP SSD quota (500 GB limit in `us-central1`) |
| OS | Ubuntu / Debian with Desktop Environment | Full GUI for development, browser-based visualization testing |
| Region | `us-central1` | Low-cost, good connectivity |

### GCS Bucket

Data persistence is handled via Google Cloud Storage:

```bash
chmod +x infra/gcs_setup.sh
./infra/gcs_setup.sh
```

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
| Infrastructure | `signal`, `switch`, `crossing`, `level_crossing` | Track infrastructure |
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

### Phase 5: RL Simulation Dashboard
- **Interactive Station Selection:** The `/api/stations` endpoint serves named stations as searchable dropdowns from the pre-built `stations.json` registry (produced by `05b_build_station_registry.py`). The registry filters out non-mainline stations via OSM tags, uses 500m spatial clustering with fuzzy name matching to merge duplicates, and determines platform counts via a 4-tier priority (curated overrides → OSM platform geometries with 50m track-proximity validation → OSM `platforms` tag → default of 2). Each station entry includes its platform count and the source of that count. Results are cached in-memory.
- **Dynamic Country Config:** The dashboard uses the `ARTEMIS_COUNTRY` environment variable to dynamically load the appropriate country's network, stations, and RL environment configuration.
- **Dynamic Train Spawning:** Users queue any number of trains with specific start/end stations. The `ArtemisTrainEnv` snaps coordinates to nearest graph nodes using the KD-Tree and computes A* paths.
- **Auto-Termination:** The simulation server terminates automatically when all trains reach their destinations.
- **Decentralized Inference:** The PPO model is called per-train per-tick with each train's local radar observation, producing independent speed decisions.

---

## ⚠️ Important Notes

1. **Google Maps API Key:** The dashboard requires a valid `GOOGLE_MAPS_API_KEY` in your `.env` file to render the map tiles.
2. **Overpass API limitations**: The Overpass API will timeout for large countries (US, Russia, China). Always use the Geofabrik pipeline for these.
3. **Disk space**: Full pipeline needs ~200 GB for all 14 countries' PBF files.
4. **Memory Optimization**: The pipeline uses a custom streaming KML writer. (Note: The `osmium-tool` pre-filtering step was previously used to keep RAM usage low, but has been bypassed since upgrading the VM to 96GB RAM, allowing direct raw extraction).
5. **C++ Compiler Required**: `build-essential` and `python3.10-dev` are required for compiling C extensions (`cykhash`, `pyrosm`).
6. **Global Python:** Dependencies are installed globally via `pip3 install --user`. No virtual environment is used.

---

## 📄 License

This project uses OpenStreetMap data, which is © OpenStreetMap contributors and available under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).
