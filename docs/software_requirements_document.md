# ARTEMIS Software Requirements Document (SRD)

**Project Name:** ARTEMIS (Autonomous Railway Throughput & Management Intelligent System)  
**Version:** 2.0  
**Last Updated:** 2026-08-20  

---

## 1. System Overview & Goals

ARTEMIS is an intelligent system designed to autonomously manage railway throughput, optimize train scheduling, and simulate complex intercity and international routing at a global scale.

The project is divided into the following phases:

| Phase | Name | Status |
|-------|------|--------|
| **Phase 1** | Data Acquisition & Pre-processing | ✅ Complete |
| **Phase 2** | Network Graph Construction | ✅ Complete |
| **Phase 3** | Spatial Indexing, Routing & Visualization | ✅ Complete |
| **Phase 4** | Multi-Agent Train Simulation (RL) | 🔲 Not Started |

---

## 2. System Architecture

### 2.1 Infrastructure (GCP)
- **Compute:** Google Cloud Vertex AI Notebook Instance.
  - Machine Type: `n2d-highmem-16` (16 vCPUs, 128 GB RAM).
  - Boot Disk: 200 GB `PD_SSD` (fits GCP `SSD_TOTAL_GB` quota of 500 GB in `us-central1`).
  - vCPU Quota: 16 N2D CPUs (fits GCP `N2D_CPUS` quota limit).
- **Storage (Cloud):** Google Cloud Storage bucket `gs://artemis-railway-data` hosted in `us-central1`.
  - Configured with Uniform Bucket-Level Access.
  - Lifecycle Rules demoting old raw files to Nearline/Coldline storage.

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
| `google-cloud-storage` | ≥ 2.14.0 | GCS integration |
| `osmium` | ≥ 3.7.0 | PBF streaming pre-filter |
| `simplekml` / `fastkml` | ≥ 1.3.6 / ≥ 0.12 | KML generation |

---

## 3. Phase 1: Data Acquisition & Pre-processing

### 3.1 Pipeline Steps
1. **Download (`01_download_pbf.py`)**: Concurrently fetches `.osm.pbf` files for 14 countries from Geofabrik using multi-threading.
2. **Extract (`02_extract_railway.py`)**:
   - *Prefilter:* Uses the C++ `osmium-tool` binary to stream the massive PBF files and discard all non-railway data, outputting a tiny, temporary `.osm.pbf` file (zero-memory overhead).
   - *Parse:* Uses `pyrosm` to read the filtered PBF into GeoPandas DataFrames. Extracts tracks (`rail`, `narrow_gauge`) and stations (`station`, `halt`). Saves as `.geojson`.
3. **Convert (`03_convert_to_kml.py`)**: Reads the GeoJSON and utilizes a custom *Streaming XML Generator* to dynamically write `.kml` text directly to disk. Applies color-coding (Red for mainline, Orange for stations).
4. **Upload (`04_upload_to_gcs.py`)**: Uploads the raw PBFs, GeoJSON, and KML files to the GCS bucket concurrently.

### 3.2 Data Flow
```
.osm.pbf (Geofabrik) → osmium filter → pyrosm → .geojson → Streaming KML → .kml → GCS Bucket
```

### 3.3 Optimizations
- **Concurrency:** I/O tasks run with 16 parallel workers. Memory-intensive `pyrosm` extraction capped at 4.
- **Memory Safety:** `osmium` pre-filtering prevents OOM on 10 GB+ files. Streaming KML writer prevents OOM on large XML DOMs.

---

## 4. Phase 2: Network Graph Construction

### 4.1 Script: `05_build_network_graph.py`

**Goal:** Transform raw GeoJSON track coordinates into a fully connected, traversable directed graph `G(V, E)`.

**Architecture:**
1. **GCS Integration:** Automatically downloads missing `[country]_tracks.geojson` and `[country]_stations.geojson` from `gs://artemis-railway-data/processed/geojson/`.
2. **Geometry Processing:** Handles both `LineString` and `MultiLineString` geometries. MultiLineStrings are exploded into individual segments.
3. **Node Creation:** Every GPS coordinate point in a track becomes a node. Node IDs are the full-precision `lon,lat` string representation, ensuring that overlapping track endpoints from different OSM ways are automatically merged.
4. **Edge Creation:** Sequential coordinate pairs along a track become directed edges. Reverse edges are also added (bidirectional simplification). Edges carry attributes: `osm_id`, `name`, `railway`, `maxspeed`, `gauge`, `electrified`.
5. **Output:** Saved as `.graphml` per country to `data/processed/graph/[country]/`.
6. **Parallelism:** `ProcessPoolExecutor` distributes graph construction across all 16 vCPUs.

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

## 5. Phase 3: Spatial Indexing, Routing & Visualization

### 5.1 Routing Engine: `06_spatial_routing.py`

**`RailwayRouter` class:**
1. **Graph Loading:** Loads `.graphml` files. Supports multiple countries via `nx.compose()` for inter-country routing.
2. **Connected Component Extraction:** Extracts the Largest Strongly Connected Component to remove disconnected spurs and guarantee routability.
3. **KD-Tree Spatial Index:** `scipy.spatial.cKDTree` built from all node coordinates. Provides O(log n) nearest-neighbor lookup for snapping arbitrary GPS coordinates to the nearest physical railway node.
4. **A* Pathfinding:** Uses `networkx.astar_path()` with the Haversine formula as both the edge weight function and the heuristic. Haversine calculates the great-circle distance between two GPS points on the Earth's surface.
5. **Inter-Country Routing:** When two different countries are specified, both graphs are loaded and composed into a single unified graph. Border-crossing tracks from Geofabrik extracts overlap, so the same node coordinates exist in both datasets, naturally bridging the border.

### 5.2 Visualization Server: `07_visualization_server.py`

**FastAPI + Leaflet.js web application:**
- **Dynamic Configuration:** All 14 countries are loaded from `config/countries.json`.
- **Map Click Mode:** Select Start/End country, click the map to place markers. The API calculates and draws the route.
- **Station Select Mode:** Dropdown selection of real named train stations parsed from `[country]_stations.geojson`. Station lists are cached in-memory after first load.
- **Auto-Pan:** Map automatically centers on the selected country.
- **API Endpoints:**
  - `GET /` — Serves the interactive map HTML.
  - `GET /api/stations?country=xxx` — Returns named stations with GPS coordinates.
  - `POST /api/route` — Accepts start/end country + coordinates, returns the route as a coordinate array with distance.

### 5.3 GCS Backup: `backup_graphs.py`
Uploads all generated `.graphml` files from `data/processed/graph/` to `gs://artemis-railway-data/processed/graph/` for persistence across VM lifecycle.

### 5.4 Verified Routing Example
- **Route:** London (51.5074, -0.1278) → Edinburgh (55.9533, -3.1883)
- **Network:** United Kingdom (581,833 connected nodes)
- **Result:** 6,722 nodes traversed, 637.23 km total distance
- **Real-World Comparison:** East Coast Main Line is ~632 km. ✅

---

## 6. Phase 4: Multi-Agent Train Simulation (Planned)

### 6.1 Goal
Build a custom OpenAI Gymnasium reinforcement learning environment where multiple train agents navigate the physical railway graph simultaneously.

### 6.2 Architecture (Draft)
1. **Environment:** Custom `gymnasium.Env` wrapping the Phase 3 `RailwayRouter`.
2. **Agents:** Each train agent occupies a node on the graph and can move along edges.
3. **Actions:** Accelerate, Brake, Switch Track, Wait.
4. **Observations:** Current position, nearby trains, track attributes (maxspeed, gauge), signal states.
5. **Rewards:** Arriving at destination quickly, maintaining schedule.
6. **Penalties:** Collisions with other trains, exceeding track `maxspeed`, signal violations.

---

## 7. Non-Functional Requirements

- **Scalability:** The system handles graphs with millions of nodes (US: 4.6M nodes) within 128 GB RAM.
- **Persistence:** All intermediate and final outputs are backed up to GCS. The VM can be safely deleted and recreated between phases.
- **Cost Efficiency:** VM is stopped between work sessions. Lifecycle rules demote old GCS objects to cheaper storage tiers.
- **Portability:** All scripts use relative paths from the project root. Configuration is centralized in `config/countries.json`.
