# ARTEMIS — Project History & Development Timeline

This document records the chronological development of ARTEMIS, including all major decisions, changes, bug fixes, and architectural pivots made during the project.

---

## Phase 1: Data Acquisition & Pre-processing

### 2026-08-18 — Project Inception & Infrastructure Setup
- **Created** initial project structure: `config/`, `data_preparation/`, `core_engine/`, `infra/`, `docs/`, `data/`.
- **Defined** the 14 target countries in `config/countries.json` with Geofabrik download URLs, OSM relation IDs, and Overpass area IDs.
- **Created** `infra/gcs_setup.sh` to provision the GCS bucket `gs://artemis-railway-data` in `us-central1` with Uniform Bucket-Level Access and lifecycle rules for cost optimization.
- **Created** `infra/vm_setup.sh` to provision a GCP Compute Engine instance for desktop virtualization.
  - Initial machine type: `e2-highmem-8` (8 vCPUs, 64 GB RAM).
  - Startup script auto-installs: `gdal-bin`, `libgdal-dev`, `osmium-tool`, `pyrosm`, `geopandas`, `shapely`, `simplekml`, `fastkml`, `networkx`.

### 2026-08-18 — Pipeline Scripts Development
- **Wrote** `data_preparation/01_download_pbf.py` — Multi-threaded Geofabrik PBF downloader with MD5 verification and resume support.
- **Wrote** `data_preparation/02_extract_railway.py` — Two-stage extraction:
  1. `osmium` streaming pre-filter (C++ binary, zero-memory) to strip non-railway data.
  2. `pyrosm` parsing of filtered PBF into GeoPandas DataFrames.
  - **Key Decision:** Capped `pyrosm` parallelism at 4 workers (vs. 16 for I/O tasks) to prevent OOM on large datasets (US: 10 GB PBF).
- **Wrote** `data_preparation/03_convert_to_kml.py` — Custom Streaming XML Generator that writes KML directly to disk line-by-line, bypassing the `simplekml` DOM builder which crashed on large datasets.
- **Wrote** `data_preparation/04_upload_to_gcs.py` — Concurrent upload to GCS bucket with proper directory structure.
- **Wrote** `data_preparation/pipeline.py` — Master orchestrator supporting `--countries`, `--steps`, and `--overpass` flags.
- **Wrote** `data_preparation/overpass_fallback.py` — Overpass API client for small countries or targeted queries.

### 2026-08-18 — Phase 1 Execution & Verification
- Successfully extracted railway data for all 14 countries.
- KML files uploaded to GCS and verified in Google Earth.
- **Created** `docs/software_requirements_document.md` (v1.0) covering Phase 1 architecture and Phase 2 blueprint.

---

## Phase 2: Network Graph Construction

### 2026-08-20 — VM Upgrade for Graph Processing
- **Problem:** The `e2-highmem-8` VM was insufficient for building large country graphs in parallel.
- **Change:** Upgraded `infra/vm_setup.sh` to `n2d-highmem-32` (32 vCPUs, 256 GB RAM).
- **GCP Quota Hit:** `CPUS_ALL_REGIONS` quota exceeded (limit: 32 globally, but N2D CPUS limit was 16).
- **Resolution:** Downgraded to `n2d-highmem-16` (16 vCPUs, 128 GB RAM). Updated `vm_setup.sh`.
- **GCP Quota Hit (2nd):** `SSD_TOTAL_GB` quota exceeded (limit: 500 GB in `us-central1`). Boot disk was set to 500 GB.
- **Resolution:** Reduced boot disk to 200 GB. Updated `vm_setup.sh`.

### 2026-08-20 — Graph Builder Development
- **Wrote** `data_preparation/05_build_network_graph.py`.
- **Architecture:** Reads GeoJSON tracks from local disk (auto-downloaded from GCS), iterates over geometries using fast `itertuples()`, creates NetworkX DiGraph nodes/edges, exports as `.graphml`.
- **Parallelism:** `ProcessPoolExecutor` with `os.cpu_count()` workers (16).

### 2026-08-20 — Bug Fix: `countries.json` Structure
- **Bug:** `AttributeError: 'list' object has no attribute 'keys'`.
- **Root Cause:** `countries.json` stores countries as a list under the `"countries"` key, not as a dict. The code was calling `.keys()` on a list.
- **Fix:** Changed `config.get("countries", {}).keys()` to `[c["code"] for c in config.get("countries", [])]`.

### 2026-08-20 — Phase 2 Execution (First Run)
- Successfully built GraphML files for all 14 countries.
- **Initial node counts were low** (e.g., UK: 60,287 nodes). This was later identified as a geometry parsing issue.

---

## Phase 3: Spatial Indexing, Routing & Visualization

### 2026-08-20 — Routing Engine Development
- **Wrote** `core_engine/06_spatial_routing.py` with the `RailwayRouter` class.
- **Architecture:**
  1. Load GraphML.
  2. Extract Largest Strongly Connected Component.
  3. Build `scipy.spatial.cKDTree` from node coordinates.
  4. Route using `networkx.astar_path()` with Haversine-based heuristic and edge weights.
- **Added** `scipy>=1.11.0` to `infra/vm_setup.sh` pip install block.

### 2026-08-20 — Bug Fix: Disconnected Graph Routing
- **Bug:** London → Edinburgh returned "No path found."
- **Root Cause:** The KD-Tree snapped the start/end coordinates to nodes on disconnected local rail spurs that were not part of the main national network.
- **Fix:** Added extraction of the Largest Strongly Connected Component (`nx.strongly_connected_components`) before building the KD-Tree. This ensures routing only occurs on the main connected network.
- **New Bug Found:** The connected component only had 978 nodes (out of 60,287). Something was fundamentally wrong with graph construction.

### 2026-08-20 — Critical Bug Fix: MultiLineString + Coordinate Rounding
- **Root Cause (MultiLineString):** GeoPandas sometimes merges multiple discontinuous railway segments into `MultiLineString` geometries. The Phase 2 builder was only processing `LineString` objects, silently dropping all `MultiLineString` entries — losing the vast majority of the rail network.
- **Root Cause (Rounding):** Node IDs were created by rounding coordinates to 5 decimal places (`f"{lon:.5f},{lat:.5f}"`). This caused some track endpoints that should connect at intersections to be assigned different node IDs due to floating-point rounding differences.
- **Fix in `05_build_network_graph.py`:**
  1. Added `MultiLineString` handling: explode into individual `LineString` segments.
  2. Changed node IDs to use full-precision floats: `f"{lon},{lat}"`.
  3. Added `from shapely.geometry import LineString, MultiLineString`.

### 2026-08-20 — Phase 2 Re-execution (Second Run)
- Rebuilt all 14 country graphs with the fix.
- **Dramatic improvement:** UK went from 60,287 → 608,729 nodes. US: 4,636,941 nodes.
- The connected component for UK was now 581,833 nodes (95.6% of the raw graph).

### 2026-08-20 — First Successful Route
- **London → Edinburgh:** 6,722 nodes, 637.23 km. The real East Coast Main Line is ~632 km. ✅

### 2026-08-20 — Visualization Server Development
- **Wrote** `core_engine/07_visualization_server.py` using FastAPI + Leaflet.js.
- Initially hardcoded 5 countries for testing.
- **Added** `fastapi>=0.100.0` and `uvicorn>=0.23.0` to `infra/vm_setup.sh`.

### 2026-08-20 — Bug Fix: Jupyter Proxy Path
- **Bug:** Clicking the map showed "Error communicating with server."
- **Root Cause:** The frontend used an absolute fetch path (`/api/route`) which resolved to the Jupyter notebook's root domain, not the proxied FastAPI server.
- **Fix:** Changed `fetch('/api/route', ...)` to `fetch('api/route', ...)` (relative path).

### 2026-08-20 — Feature: Map Auto-Pan on Country Change
- **Request:** Selecting a different country in the dropdown should move the map to that country.
- **Implementation:** Added `countryCenters` and `countryZooms` JS objects. Added `change` event listener on the dropdown to call `map.setView()` and `clearMap()`.

### 2026-08-20 — Feature: All 14 Countries in Dropdown
- **Issue:** Only 5 countries were hardcoded in the dropdown.
- **Fix:** Server now dynamically reads `config/countries.json` and generates `<option>` tags for all 14 countries.

### 2026-08-20 — Feature: Inter-Country Routing
- **Request:** Routing between different countries (e.g., France → Germany).
- **Implementation:** Modified `RailwayRouter.__init__()` to accept a list of country codes. Loads all GraphMLs and merges them with `nx.compose()` before extracting the combined Largest Strongly Connected Component. The visualization server constructs a sorted, deduplicated cache key (e.g., `"france,germany"`) for the router cache.

### 2026-08-20 — Feature: Station-to-Station Routing
- **Request:** Allow selecting real train stations from a dropdown instead of clicking the map.
- **Implementation:**
  - New API endpoint: `GET /api/stations?country=xxx` — Parses `[country]_stations.geojson`, filters for named stations, returns sorted list with GPS coordinates.
  - New UI: Toggle tab (Map Click / Station Select). Station mode shows two country dropdowns and two station dropdowns. Selecting a country loads its stations asynchronously.
  - Results are cached in `station_cache` dict to avoid re-parsing large GeoJSON files.

### 2026-08-20 — Bug Fix: Station Dropdown Stuck on "Loading..."
- **Bug:** Selecting a country in Station Select mode showed "Loading..." indefinitely.
- **Root Cause:** The `/api/stations` endpoint was defined as `async def`, which blocks the main event loop while parsing massive GeoJSON files. FastAPI runs `async def` on the main thread; `def` functions are automatically offloaded to a background thread pool.
- **Fix:** Changed `async def get_stations` to `def get_stations`. Added in-memory caching (`station_cache` dict) so subsequent requests for the same country return instantly.

### 2026-08-20 — GCS Backup Script
- **Wrote** `data_preparation/backup_graphs.py` to upload all `.graphml` files to `gs://artemis-railway-data/processed/graph/[country]/`.

### 2026-08-20 — Documentation Update
- Updated `README.md` to reflect all three completed phases, new scripts, updated VM specifications, and full pipeline architecture.
- Updated `docs/software_requirements_document.md` to v2.0 with verified node/edge counts, Phase 3 architecture, and Phase 4 draft.
- Created `docs/project_history.md` (this file).

---

## Phase 4: Multi-Agent Train Simulation (Legacy)

### 2026-08-20 — Simulation Engine Development (Discrete-Event)
- **Wrote** `core_engine/08_simulation_engine.py`.
- **Architecture:**
  - `TrainAgent`: Dataclass tracking train status, speed (km/h), route progress, and blocked ticks.
  - `RailwaySimulation`: Discrete-event engine (tick-based) that advances trains along their pre-computed A* routes.
  - **Collision Avoidance:** Uses an `edge_occupancy` dict (`"u->v": train_id`) to enforce strict 1-train-per-edge capacity limits. Trains stop and enter `BLOCKED` status if the next track segment is occupied.
- **Added** a headless CLI test harness to spawn `N` trains at random stations and run `M` simulation ticks, printing stats every 50 ticks.

### 2026-08-20 — Live Simulation Dashboard (Legacy)
- **Updated** `core_engine/07_visualization_server.py`.
- Added `/api/sim/*` endpoints for the discrete-event simulation.
- Added a Dark Mode Leaflet dashboard at `/simulation`.

> **Note:** This discrete-event simulation was later superseded by the RL-based approach in Phase 5.

---

## Phase 5: Reinforcement Learning — v1 (Centralized PPO)

### 2026-08-29 — RL v1 Architecture
- **Created** `versions/v1/scripts/train_env.py` — Custom Gymnasium environment.
  - **Observation:** Flattened state of all trains simultaneously: `Box(shape=(num_agents * 3,))`.
  - **Action Space:** `MultiDiscrete([3] * num_agents)` — Brake / Maintain / Accelerate, one action per train.
  - **Reward:** Progress toward destination (+1), collision penalty (-1000), arrival bonus (+100), time penalty (-1).
- **Created** `versions/v1/scripts/train_ppo.py` — PPO training script.
- **Trained** on UK network, saved to `versions/v1/models/ppo_artemis_uk_final.zip`.
- **Limitation Identified:** The centralized observation space is tightly coupled to `num_agents`. The model cannot be used with a different number of trains without retraining. This was the primary motivation for v2.

---

## Phase 5: Reinforcement Learning — v2 (Decentralized Shared-Radar PPO)

### 2026-08-29 — RL v2 Architecture
- **Created** `versions/v2/scripts/train_env_v2.py` — Decentralized Gymnasium environment.
  - **Observation per train:** `Box(shape=(3,))` — `[current_speed, edge_speed_limit, dist_to_nearest_train]`.
  - **Action per train:** `Discrete(3)` — Brake / Maintain / Accelerate.
  - Each train only sees its own local radar, not the global state.
  - **Reward Function:**
    - +1.0 for making progress (speed > 0).
    - +100.0 for reaching the destination.
    - -1.0 time penalty per step.
    - -5.0 for exceeding the track speed limit.
    - -100.0 safe braking distance penalty (within 2 km at speed > 50 km/h).
    - -10,000.0 for collision (two trains within 0.5 km).
  - **Dynamic Station Injection:** `reset()` accepts optional `train_configs` with GPS coordinates.
  - **Hot-Add Agents:** `add_agent()` dynamically injects new trains into a running simulation.
- **Created** `versions/v2/scripts/train_ppo_v2.py` — Training script with `FlattenMultiAgentVecEnv`.
  - **Key Innovation:** A custom `VecEnv` wrapper "unwraps" the multi-agent environment so Stable-Baselines3 sees each train as an independent single-agent environment. This trains a **single shared policy** that is applied to every train independently at inference time.
  - Supports `SubprocVecEnv` for parallel rollout across multiple CPU cores.
- **Trained** on UK railway network with 4 concurrent agents.
  - Final model: `ppo_artemis_uk_final.zip`.

### 2026-08-29 — RL Simulation Controller
- **Created** `versions/v2/scripts/rl_sim_controller.py`.
  - Bridges the pretrained PPO model with the visualization server.
  - Runs the RL environment in a background thread, calling `model.predict()` per-train per-tick.
  - Exposes `get_state()` and `get_active_edges()` for real-time frontend polling.
  - **Hot-Add Trains:** Supports `add_trains()` to inject new trains into a running simulation.
  - **Idle Wait:** When all trains arrive, the controller waits for new trains to be added instead of terminating the server.

### 2026-08-29 — Interactive Train Dispatcher UI
- **Rewrote** `core_engine/07_visualization_server.py` as a standalone RL simulation server.
  - **Removed** legacy routing UI (`serve_ui()`, `/api/route`, inter-country routing).
  - **Removed** legacy discrete-event simulation endpoints (`/api/sim/*`).
  - **Added** `GET /api/stations` — Fetches real UK railway stations for the dropdowns.
  - **Added** `GET /api/network` — Returns UK railway tracks as GeoJSON for map rendering.
  - **Added** `POST /api/trains/add` — Deploys trains (starts sim if not running, or hot-adds to running sim).
  - **Added** `POST /api/trains/clear` — Stops simulation and clears all trains.
  - **Added** `GET /api/trains/state` — Returns real-time positions of all trains.
  - **Added** "Train Dispatcher" panel to the frontend:
    - Two searchable station dropdowns (Origin/Destination).
    - "Deploy Train" button to add a route and start the simulation.
    - "Clear All Trains" button to reset.
    - Any number of trains can be deployed, including during a running simulation.
  - Dashboard served at root URL (`/`).

### 2026-08-29 — Dynamic Station Injection
- **Updated** `versions/v2/scripts/train_env_v2.py` — `reset()` now accepts optional `train_configs`.
  - If provided, maps requested GPS coordinates to nearest graph nodes using the KD-Tree.
  - If not provided, falls back to random start/end station selection (backward compatible with training).

### 2026-08-29 — Bug Fix: Premature Simulation Reset
- **Bug:** One train would reach its destination, then the entire simulation would restart from scratch.
- **Root Cause:** `self.max_steps` was set to 1,000 in the environment. Long routes with >1,000 graph nodes would trigger a `truncated` flag, causing the controller to reset the entire environment.
- **Fix:** Increased `self.max_steps` to 20,000.

### 2026-08-29 — Environment Migration
- **Migrated** from virtual environment (`venv`) to global Python dependencies.
- Installed `build-essential` and `python3.10-dev` for C-extension compilation (`cykhash`, `pyrosm`).
- All packages installed via `pip3 install --user` to `~/.local/`.

### 2026-08-31 — UI Migration to Google Maps
- **Migrated** the visualization server frontend (`07_visualization_server.py`) from Leaflet.js (Dark Mode) to Google Maps API (Light Mode/Silver style).
- **Updated** map markers to use standard Google Maps markers.
- **Added** `python-dotenv` support for loading the `GOOGLE_MAPS_API_KEY` environment variable.

### 2026-09-09 — Desktop Virtualization Setup Script
- **Created** `infra/desktop_virtualization_setup.sh` — Standalone script to provision a headless Ubuntu server into a full XFCE + XRDP virtual desktop.
  - Installs XFCE desktop, XRDP for remote access, Python 3.10+, Google Chrome, Antigravity IDE, and all ARTEMIS geospatial dependencies.
  - Targets Ubuntu 22.04 LTS on GCP `e2-standard-4` or higher.

### 2026-09-09 — Model Weight Re-export for GitHub
- **Created** `versions/v2/scripts/reexport_weights.py`.
  - Extracts clean `.pth` (PyTorch) weight files from SB3 `.zip` archives.
  - SB3 `.zip` files contain Python pickle data (`data` blob) which triggers antivirus false positives (e.g., GitHub, Windows Defender).
  - The `_weights/` directories contain only safe `.pth` files and are committed to GitHub instead.
  - Also re-exports v1 model weights if available.
- **Created** `versions/v1/models/ppo_artemis_uk_weights/` — Clean v1 policy weights.
- **Created** `versions/v2/models/ppo_artemis_uk_weights/` — Clean v2 policy weights.
- **Updated** `.gitignore` to exclude all `.zip` model files and commit only the `_weights/` directories.

---

## Current Status (2026-09-09)

| Phase | Name | Status |
|-------|------|--------|
| **Phase 1** | Data Acquisition & Pre-processing | ✅ Complete |
| **Phase 2** | Network Graph Construction | ✅ Complete |
| **Phase 3** | Spatial Indexing & Routing Engine | ✅ Complete |
| **Phase 4** | Discrete-Event Simulation (Legacy) | ⚠️ Superseded by RL |
| **Phase 5 (v1)** | Centralized PPO RL | ✅ Complete (Deprecated) |
| **Phase 5 (v2)** | Decentralized Shared-Radar PPO RL | ✅ Complete & Active |
| **Dashboard** | Interactive Train Dispatcher | ✅ Complete |

### Active Architecture
- The **visualization server** (`07_visualization_server.py`) serves as an RL simulation dashboard at `http://127.0.0.1:8000/`.
- Users select specific UK stations, deploy any number of trains, and watch the RL agent route them in real-time.
- Trains can be hot-added to a running simulation.
- The pretrained v2 PPO model processes each train's local radar independently, enabling **arbitrary scaling** without retraining.
- Model weights are distributed as clean `.pth` files to avoid antivirus false positives.
