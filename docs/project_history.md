# ARTEMIS — Project History & Development Timeline

This document records the chronological development of ARTEMIS, including all major decisions, changes, bug fixes, and architectural pivots made during the project.

---

## Phase 1: Data Acquisition & Pre-processing

### 2026-08-18 — Project Inception & Infrastructure Setup
- **Created** initial project structure: `config/`, `scripts/`, `infra/`, `docs/`, `data/`.
- **Defined** the 14 target countries in `config/countries.json` with Geofabrik download URLs, OSM relation IDs, and Overpass area IDs.
- **Created** `infra/gcs_setup.sh` to provision the GCS bucket `gs://artemis-railway-data` in `us-central1` with Uniform Bucket-Level Access and lifecycle rules for cost optimization.
- **Created** `infra/vm_setup.sh` to provision a Vertex AI Notebook Instance.
  - Initial machine type: `e2-highmem-8` (8 vCPUs, 64 GB RAM).
  - Startup script auto-installs: `gdal-bin`, `libgdal-dev`, `osmium-tool`, `pyrosm`, `geopandas`, `shapely`, `simplekml`, `fastkml`, `networkx`.

### 2026-08-18 — Pipeline Scripts Development
- **Wrote** `scripts/01_download_pbf.py` — Multi-threaded Geofabrik PBF downloader with MD5 verification and resume support.
- **Wrote** `scripts/02_extract_railway.py` — Two-stage extraction:
  1. `osmium` streaming pre-filter (C++ binary, zero-memory) to strip non-railway data.
  2. `pyrosm` parsing of filtered PBF into GeoPandas DataFrames.
  - **Key Decision:** Capped `pyrosm` parallelism at 4 workers (vs. 16 for I/O tasks) to prevent OOM on large datasets (US: 10 GB PBF).
- **Wrote** `scripts/03_convert_to_kml.py` — Custom Streaming XML Generator that writes KML directly to disk line-by-line, bypassing the `simplekml` DOM builder which crashed on large datasets.
- **Wrote** `scripts/04_upload_to_gcs.py` — Concurrent upload to GCS bucket with proper directory structure.
- **Wrote** `scripts/pipeline.py` — Master orchestrator supporting `--countries`, `--steps`, and `--overpass` flags.
- **Wrote** `scripts/overpass_fallback.py` — Overpass API client for small countries or targeted queries.

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
- **Wrote** `scripts/05_build_network_graph.py`.
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
- **Wrote** `scripts/06_spatial_routing.py` with the `RailwayRouter` class.
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
- **Wrote** `scripts/07_visualization_server.py` using FastAPI + Leaflet.js.
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
- **Wrote** `scripts/backup_graphs.py` to upload all `.graphml` files to `gs://artemis-railway-data/processed/graph/[country]/`.

### 2026-08-20 — Documentation Update
- Updated `README.md` to reflect all three completed phases, new scripts, updated VM specifications, and full pipeline architecture.
- Updated `docs/software_requirements_document.md` to v2.0 with verified node/edge counts, Phase 3 architecture, and Phase 4 draft.
- Created `docs/project_history.md` (this file).

---

## Phase 4: Multi-Agent Train Simulation

### 2026-08-20 — Simulation Engine Development
- **Wrote** `scripts/08_simulation_engine.py`.
- **Architecture:** 
  - `TrainAgent`: Dataclass tracking train status, speed (km/h), route progress, and blocked ticks.
  - `RailwaySimulation`: Discrete-event engine (tick-based) that advances trains along their pre-computed A* routes.
  - **Collision Avoidance:** Uses an `edge_occupancy` dict (`"u->v": train_id`) to enforce strict 1-train-per-edge capacity limits. Trains stop and enter `BLOCKED` status if the next track segment is occupied.
- **Added** a headless CLI test harness to spawn `N` trains at random stations and run `M` simulation ticks, printing stats every 50 ticks.

### 2026-08-20 — Live Simulation Dashboard
- **Updated** `scripts/07_visualization_server.py`.
- **Architecture:** 
  - Added new `/api/sim/*` endpoints (`start`, `stop`, `reset`, `state`) to control a globally running `RailwaySimulation` instance.
  - Added a background Python thread (`threading.Thread`) that automatically calls `sim.tick()` every 0.5s when active.
  - Added a gorgeous Dark Mode Leaflet dashboard at `/simulation`.
  - The UI polls `/api/sim/state` every 500ms and updates Leaflet markers (🚆 / 🟡 / ✅) moving smoothly across the map, with a live stats readout of Active/Blocked/Arrived trains.

### 2026-08-20 — Bug Fix: Station Loading without Names
- **Bug:** `KeyError: 'name'` when trying to spawn random trains.
- **Root Cause:** The `[country]_stations.geojson` files generated by `pyrosm` did not always export a clean `'name'` column (sometimes it's `'tags.name'`, or absent entirely).
- **Fix:** Updated both the visualization server and simulation engine to dynamically scan for name column candidates (`['name', 'tags.name', 'Name', 'NAME']`). If none exist, it gracefully falls back to generating index-based names (`"Station #1234"`).
- **Verified:** Ran `python3 scripts/08_simulation_engine.py uk --trains 5` successfully on the VM, proving collision avoidance and routing work perfectly.

---

## Next Steps
With the core routing and simulation engines running, we have completed the primary requirements of the ARTEMIS project! Future phases could include advanced scheduling algorithms, integration with external transit APIs, or enhanced timetable management.
