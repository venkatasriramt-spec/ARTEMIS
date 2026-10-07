# ARTEMIS Software Requirements Document (SRD)

**Project Name:** ARTEMIS (Autonomous Railway Throughput & Management Intelligent System)  
**Version:** 4.1  
**Last Updated:** 2026-10-07  

---

## 1. System Overview & Goals

ARTEMIS is an intelligent system designed to autonomously manage railway throughput, optimize train routing using reinforcement learning, and simulate complex intercity navigation on real-world railway networks at global scale.

The project is divided into the following phases:

| Phase | Name | Status |
|-------|------|--------|
| **Phase 1** | Data Acquisition & Pre-processing | ✅ Complete |
| **Phase 2** | Network Graph Construction | ✅ Complete |
| **Phase 3** | Spatial Indexing, Routing & Visualization | ✅ Complete |
| **Phase 4** | Discrete-Event Simulation (Legacy) | ⚠️ Superseded |
| **Phase 5 (v1)** | Centralized PPO RL | ✅ Complete (Deprecated) |
| **Phase 5 (v2)** | Decentralized Shared-Radar PPO RL | ✅ Complete & Active |
| **Dashboard** | Interactive Train Dispatcher UI | ✅ Complete |

---

## 2. System Architecture

### 2.1 Infrastructure
- **Local Development:** Ubuntu/Debian desktop with Python 3.10+.
  - Dependencies installed globally via `pip3 install --user`.
  - C++ compiler (`build-essential`) required for extensions like `cykhash` and `pyrosm`.
- **Cloud (Optional):** GCP Virtual Desktop (Compute Engine instance with XFCE + XRDP desktop environment).
  - Machine Type: Adjusted as needed (e.g., `n2d-highmem-16` for heavy workloads).
  - Boot Disk: 200 GB `PD_SSD`.
  - Setup script: `infra/desktop_virtualization_setup.sh`.
- **Storage (Cloud):** Google Cloud Storage bucket `gs://artemis-railway-data` in `us-central1`.

### 2.2 Dependencies

| Library | Version | Purpose |
|---------|---------|---------|
| `pyrosm` | ≥ 0.6.1 | PBF parsing |
| `geopandas` | ≥ 0.14.0 | Spatial DataFrames |
| `shapely` | ≥ 2.0.0 | Geometry processing (LineString, MultiLineString) |
| `networkx` | ≥ 3.1 | Graph construction & A* routing |
| `scipy` | ≥ 1.11.0 | KD-Tree spatial indexing |
| `fastapi` | ≥ 0.100.0 | Web API server |
| `uvicorn` | ≥ 0.23.0 | ASGI server |
| `gymnasium` | ≥ 0.29.0 | RL environment framework |
| `stable-baselines3[extra]` | ≥ 2.0.0 | PPO agent training & inference |
| `torch` | (via SB3) | Neural network backend |
| `google-cloud-storage` | ≥ 2.14.0 | GCS integration |
| `osmium` | ≥ 3.7.0 | PBF streaming pre-filter (bypassed; retained for legacy compatibility) |
| `python-dotenv` | ≥ 1.0.0 | Environment variable loading |

### 2.3 System Dependencies (apt)
| Package | Purpose |
|---------|---------|
| `build-essential` | C/C++ compiler for extension modules |
| `python3.10-dev` | Python development headers |

---

## 3. Phase 1: Data Acquisition & Pre-processing

### 3.1 Pipeline Steps
1. **Download (`01_download_pbf.py`)**: Concurrently fetches `.osm.pbf` files for 14 countries from Geofabrik using multi-threading.
2. **Extract (`02_extract_railway.py`)**:
   - *Parse:* Uses `pyrosm` to read the raw PBF into GeoPandas DataFrames. Extracts tracks (`rail`, `narrow_gauge`), stations (`station`, `halt`), and additional attributes like `platform`. Excludes non-mainline rail (e.g., `subway`, `light_rail`, `tram`, `preserved`, `miniature`). Saves as `.geojson`. (Note: The `osmium-tool` pre-filter step was bypassed after upgrading the VM to 96GB RAM, allowing direct extraction).
3. **Convert (`03_convert_to_kml.py`)**: Custom *Streaming XML Generator* to write `.kml` directly to disk.
4. **Upload (`04_upload_to_gcs.py`)**: Uploads all files to the GCS bucket concurrently.

### 3.2 Data Flow
```
.osm.pbf (Geofabrik) → pyrosm → .geojson → stations.json (registry) & Streaming KML → .kml → GCS Bucket
```

---

## 4. Phase 2: Network Graph Construction

### 4.1 Script: `05_build_network_graph.py`

**Architecture:**
1. Reads GeoJSON tracks, handles both `LineString` and `MultiLineString` geometries.
2. Node IDs use full-precision `lon,lat` strings ensuring automatic merging of overlapping endpoints.
3. Builds bidirectional `NetworkX DiGraph` with edge attributes: `osm_id`, `name`, `railway`, `maxspeed`, `gauge`, `electrified`.
4. Exports as `.graphml` per country.

### 4.2 Verified Output (14 Countries)

| Country | Nodes | Edges |
|---------|-------|-------|
| United States | 4,636,941 | 9,449,368 |
| Russia | 2,628,513 | 5,388,733 |
| China | 2,238,883 | 4,649,718 |
| Germany | 1,719,095 | 3,503,774 |
| France | 1,509,847 | 3,068,566 |
| Japan | 751,101 | 1,532,842 |
| Canada | 649,784 | 1,323,362 |
| United Kingdom | 608,729 | 1,238,584 |
| Italy | 596,230 | 1,216,392 |
| South Africa | 533,484 | 1,088,126 |
| Australia | 451,289 | 915,884 |
| Brazil | 320,456 | 646,074 |
| Mexico | 211,744 | 431,896 |
| Argentina | 102,703 | 210,816 |

---

## 5. Phase 3: Spatial Indexing & Routing Engine

### 5.1 `06_spatial_routing.py` — `RailwayRouter` Class
1. **Graph Loading:** Loads `.graphml` files. Supports multi-country merging via `nx.compose()`.
2. **Connected Component:** Extracts Largest Strongly Connected Component.
3. **KD-Tree:** `scipy.spatial.cKDTree` for O(log n) nearest-neighbor GPS-to-node snapping.
4. **A* Pathfinding:** `networkx.astar_path()` with Haversine heuristic.

### 5.2 Verified Routing Example
- **Route:** London → Edinburgh — 6,722 nodes, 637.23 km (real: ~632 km ✅)

---

## 6. Phase 5: Reinforcement Learning

### 6.1 v1 Architecture (Deprecated)
- **Location:** `versions/v1/`
- **Observation:** `Box(shape=(num_agents * 3,))` — flattened global state.
- **Action:** `MultiDiscrete([3] * num_agents)` — joint action for all trains.
- **Limitation:** Cannot scale to different numbers of trains without retraining.

### 6.2 v2 Architecture (Active)
- **Location:** `versions/v2/`
- **Environment:** `ArtemisTrainEnv` in `train_env_v2.py`.
  - **Observation per train:** `Box(shape=(3,))` — `[speed, edge_speed_limit, dist_to_nearest_train]`.
  - **Action per train:** `Discrete(3)` — Brake / Maintain / Accelerate.
  - **Reward Function:**
    - +1.0 for making progress (speed > 0).
    - +100.0 for reaching the destination.
    - -1.0 time penalty per step.
    - -5.0 for exceeding the track speed limit.
    - -100.0 safe braking distance penalty (within 2 km at speed > 50 km/h).
    - -10,000.0 for collision (two trains within 0.5 km).
  - **Dynamic Station Injection:** `reset()` accepts optional `train_configs` with GPS coordinates, mapping them to nearest graph nodes via KD-Tree. Falls back to random stations if not provided.
  - **Hot-Add Agents:** `add_agent()` dynamically injects new trains into a running simulation.
  - **Max Steps:** 20,000 (prevents premature truncation on long routes).

- **Training:** `train_ppo_v2.py`
  - `FlattenMultiAgentVecEnv` wrapper treats each train as an independent single-agent env for SB3.
  - Trained on UK network with 4 concurrent agents.
  - Checkpointed periodically; final model: `ppo_artemis_uk_final.zip`.

- **Inference Controller:** `rl_sim_controller.py`
  - Background thread calls `model.predict(obs[i])` per train per tick.
  - Waits for more trains after all current trains arrive (no longer auto-terminates the server).
  - Supports hot-adding trains to a running simulation via `add_trains()`.

- **Weight Re-export:** `reexport_weights.py`
  - Extracts clean `.pth` (PyTorch) weight files from SB3 `.zip` archives into `_weights/` directories.
  - SB3 zips contain Python pickle data which triggers antivirus false positives on GitHub.
  - The `_weights/` directories are committed instead; the `.zip` files are git-ignored.

### 6.3 Key Design Decision: Decentralized Scaling
The v2 model processes a 3-feature local radar for **one train at a time**. Because the policy is shared and independent, the same model works with 1, 10, or 100 trains without retraining.

---

## 7. Interactive Simulation Dashboard

### 7.1 Server: `07_visualization_server.py`
- **Framework:** FastAPI + Google Maps API (Light Mode).
- **Root URL:** `http://127.0.0.1:8000/`
- **API Key:** Requires `GOOGLE_MAPS_API_KEY` environment variable (loaded via `python-dotenv` from `.env` file).
- **Country Selection:** Uses the `ARTEMIS_COUNTRY` environment variable (defaults to `uk`) to dynamically load the appropriate network, stations, and simulation environment.

### 7.6 Station Registry (`data_preparation/05b_build_station_registry.py`)
- Builds a `stations.json` registry from GeoJSON station data, snapping stations to the railway graph nodes.
- **Filtering:** Excludes non-mainline stations by inspecting OSM tags (`subway`, `light_rail`, `tram`, `preserved`, `miniature`, `disused`, `abandoned`, `construction`). Stations tagged as `usage=tourism` are also dropped. Stations belonging to `network=National Rail` are protected from tag-based exclusion.
- **Deduplication:** Uses 500m spatial clustering with fuzzy substring name matching to merge duplicate station POIs. Assigns disambiguated `display_name`s for any remaining name collisions.
- **Platform Counts (4-tier priority):**
  1. **Curated overrides** from `config/reference/uk_major_station_platforms.json`.
  2. **OSM platform geometries** (extracted via `extract_platforms_only.py`): assigned to nearest station cluster via `sjoin_nearest` (150m max). Each platform is validated to be within 50m of a track node. Platform `ref` tags are parsed and alpha suffixes stripped (e.g., `3a` → `3`) to count distinct physical platforms.
  3. **OSM `platforms` tag** on the station node itself.
  4. **Default** fallback of 2.
- **Output:** `data/processed/geojson/{country}/stations.json` — sorted by key, each entry keyed by primary OSM ID with: name, display_name, lat/lon, platform_count, platform_source, and graph_node_id.
- **Validation (`validate_station_registry.py`):**
  - Enforces non-curated platform counts ≤ 24.
  - Enforces default-sourced stations ≤ 10% of registry.
  - Checks for duplicate display names.
  - Tag-based verification: cross-references raw GeoJSON to ensure no station with forbidden tags (subway, light_rail, tram, preserved, miniature) survived filtering.
  - Hold-out comparison against `config/reference/uk_validation_holdout.json` with tolerance of ±1.
- Depends on: `geopandas`, `core_engine/06_spatial_routing.py` (RailwayRouter for node snapping and 50m track proximity validation).

### 7.2 API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serves the simulation dashboard HTML |
| `/api/stations` | GET | Returns named UK stations with GPS coords |
| `/api/network` | GET | Returns UK railway tracks as GeoJSON |
| `/api/trains/add` | POST | Deploys trains (starts sim if not running, or hot-adds) |
| `/api/trains/clear` | POST | Stops simulation and clears all trains |
| `/api/trains/state` | GET | Returns real-time positions of all trains |

### 7.3 Deploy Trains Request Payload
```json
{
  "trains": [
    {"start_lat": 51.53, "start_lon": -0.12, "end_lat": 55.95, "end_lon": -3.19},
    {"start_lat": 53.48, "start_lon": -2.24, "end_lat": 51.45, "end_lon": -2.58}
  ]
}
```

### 7.4 Frontend Features
- **Station Dropdowns:** Populated from real UK station data.
- **Train Deployment:** Deploy any number of trains with specific start/end stations. Simulation starts automatically on first deploy.
- **Hot-Add:** Deploy additional trains into a running simulation without restarting.
- **Live Map:** Google Maps markers move in real-time with emoji status indicators (🚆 en route, ✅ arrived).
- **Network Overlay:** Full UK railway network rendered as GeoJSON with active track highlighting.
- **Stats Panel:** Live active/arrived train counts.
- **Clear All:** Button to stop simulation and clear all trains.

---

## 8. Non-Functional Requirements

- **Scalability:** Handles graphs with millions of nodes (US: 4.6M). RL model scales to arbitrary train counts.
- **Persistence:** All outputs backed up to GCS.
- **Portability:** All scripts use relative paths. Configuration centralized in `config/countries.json`.
- **No Virtual Environment:** Dependencies installed globally for simplicity.
- **AV-Safe Distribution:** Model weights are re-exported as clean `.pth` files to avoid antivirus false positives on GitHub.
