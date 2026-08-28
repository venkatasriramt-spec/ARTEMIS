#!/usr/bin/env python3
"""
ARTEMIS - Visualization Server + Simulation Dashboard
=====================================================
A FastAPI backend serving:
1. Interactive Leaflet routing map (Phase 3)
2. Live train simulation dashboard (Phase 4)
"""

import os
import sys
import json
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import geopandas as gpd

# Import our router from 06_spatial_routing
base_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(base_dir / "scripts"))
try:
    from importlib import import_module
    routing_module = import_module("06_spatial_routing")
    RailwayRouter = routing_module.RailwayRouter
    sim_module = import_module("08_simulation_engine")
    RailwaySimulation = sim_module.RailwaySimulation
    add_route_from_nodes = sim_module.add_route_from_nodes
except ImportError as e:
    print(f"Error: Could not import modules: {e}")
    sys.exit(1)

app = FastAPI(title="ARTEMIS Railway Routing API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global caches
routers = {}
active_simulation = None
sim_thread = None

class RouteRequest(BaseModel):
    start_country: str
    end_country: str
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float

# Load countries config
config_path = base_dir / "config" / "countries.json"
with open(config_path, "r") as f:
    config = json.load(f)
countries_list = [c["code"] for c in config.get("countries", [])]

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serves the interactive map UI."""
    country_options = "\n".join([f'<option value="{c}">{c.replace("_", " ").title()}</option>' for c in countries_list])
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>ARTEMIS Railway Router</title>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <style>
            body, html {{ margin: 0; padding: 0; height: 100%; font-family: sans-serif; }}
            #map {{ height: 100vh; width: 100vw; }}
            #ui-box {{
                position: absolute; top: 20px; right: 20px; z-index: 1000;
                background: white; padding: 15px; border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 320px; max-height: 90vh; overflow-y: auto;
            }}
            .btn {{
                background: #2563eb; color: white; border: none; padding: 10px;
                border-radius: 4px; cursor: pointer; width: 100%; margin-top: 10px;
            }}
            .btn:hover {{ background: #1d4ed8; }}
            select, input {{ width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box; }}
            .hidden {{ display: none; }}
            .toggle-container {{ display: flex; margin-bottom: 15px; border-bottom: 2px solid #eee; }}
            .toggle-tab {{ flex: 1; text-align: center; padding: 10px; cursor: pointer; color: #666; font-weight: bold; }}
            .toggle-tab.active {{ color: #2563eb; border-bottom: 2px solid #2563eb; margin-bottom: -2px; }}
            .section {{ background: #f8fafc; padding: 10px; border-radius: 4px; margin-bottom: 10px; }}
            label {{ font-size: 13px; font-weight: bold; color: #333; }}
        </style>
    </head>
    <body>
        <div id="ui-box">
            <h2 style="margin-top:0">ARTEMIS</h2>
            
            <div class="toggle-container">
                <div class="toggle-tab active" id="tab-map" onclick="setMode('map')">Map Click</div>
                <div class="toggle-tab" id="tab-station" onclick="setMode('station')">Station Select</div>
            </div>
            
            <!-- Map Mode UI -->
            <div id="mode-map">
                <div class="section">
                    <label>Start Country:</label>
                    <select id="start-country-map" onchange="panToCountry(this.value)">{country_options}</select>
                </div>
                <div class="section">
                    <label>End Country:</label>
                    <select id="end-country-map">{country_options}</select>
                </div>
                <p style="font-size: 13px; color: #555;">
                    1. Click map for Start Point.<br>
                    2. Click map for End Point.<br>
                </p>
            </div>
            
            <!-- Station Mode UI -->
            <div id="mode-station" class="hidden">
                <div class="section">
                    <label>Start Country:</label>
                    <select id="start-country-station" onchange="loadStations('start')">
                        <option value="">Select country...</option>
                        {country_options}
                    </select>
                    <label style="margin-top: 10px; display: block;">Start Station:</label>
                    <select id="start-station" disabled><option>Select country first...</option></select>
                </div>
                
                <div class="section">
                    <label>End Country:</label>
                    <select id="end-country-station" onchange="loadStations('end')">
                        <option value="">Select country...</option>
                        {country_options}
                    </select>
                    <label style="margin-top: 10px; display: block;">End Station:</label>
                    <select id="end-station" disabled><option>Select country first...</option></select>
                </div>
                
                <button class="btn" onclick="routeByStation()">Find Route</button>
            </div>
            
            <div id="loading" class="hidden" style="color: #d97706; font-weight: bold; text-align: center; margin-top:10px;">
                Calculating Route...<br><small>(Combining graphs may take 30s+)</small>
            </div>
            
            <div id="results" class="hidden" style="margin-top: 15px; border-top: 1px solid #ddd; padding-top: 10px;">
                <strong>Distance:</strong> <span id="res-dist"></span> km<br>
                <strong>Nodes:</strong> <span id="res-nodes"></span>
            </div>
            
            <button class="btn" style="background:#dc2626;" onclick="clearMap()">Clear Map</button>
        </div>
        <div id="map"></div>

        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script>
            let currentMode = 'map';
            const map = L.map('map').setView([51.5, -0.1], 4);
            L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
                attribution: '© OpenStreetMap contributors © CARTO'
            }}).addTo(map);

            // Simple geocoder for panning
            const countryCenters = {{
                'argentina': [-38.4, -63.6], 'australia': [-25.2, 133.7], 'brazil': [-14.2, -51.9], 
                'canada': [56.1, -106.3], 'china': [35.8, 104.1], 'france': [46.2, 2.2], 
                'germany': [51.1, 10.4], 'italy': [41.8, 12.5], 'japan': [36.2, 138.2], 
                'mexico': [23.6, -102.5], 'russia': [61.5, 105.3], 'south_africa': [-30.5, 22.9], 
                'uk': [53.5, -2.5], 'us': [39.8, -98.5]
            }};

            function setMode(mode) {{
                currentMode = mode;
                document.getElementById('tab-map').classList.remove('active');
                document.getElementById('tab-station').classList.remove('active');
                document.getElementById('tab-' + mode).classList.add('active');
                
                document.getElementById('mode-map').classList.add('hidden');
                document.getElementById('mode-station').classList.add('hidden');
                document.getElementById('mode-' + mode).classList.remove('hidden');
                clearMap();
            }}

            function panToCountry(country) {{
                if (countryCenters[country]) {{
                    map.setView(countryCenters[country], 5);
                }}
            }}

            // Make sure map dropdown triggers pan initially if UK is selected
            panToCountry(document.getElementById('start-country-map').value);

            let startMarker = null;
            let endMarker = null;
            let routeLayer = null;

            map.on('click', async function(e) {{
                if (currentMode !== 'map') return;
                
                if (!startMarker) {{
                    startMarker = L.marker(e.latlng).addTo(map).bindPopup("Start").openPopup();
                }} else if (!endMarker) {{
                    endMarker = L.marker(e.latlng).addTo(map).bindPopup("End").openPopup();
                    
                    const startCountry = document.getElementById('start-country-map').value;
                    const endCountry = document.getElementById('end-country-map').value;
                    await triggerRouteApi(startCountry, endCountry, startMarker.getLatLng(), endMarker.getLatLng());
                }}
            }});
            
            async function loadStations(type) {{
                const country = document.getElementById(type + '-country-station').value;
                const select = document.getElementById(type + '-station');
                if (!country) return;
                
                select.innerHTML = '<option>Loading...</option>';
                select.disabled = true;
                
                try {{
                    const response = await fetch('api/stations?country=' + country);
                    const stations = await response.json();
                    
                    select.innerHTML = '<option value="">Select a station...</option>';
                    stations.forEach(st => {{
                        select.innerHTML += `<option value="${{st.lat}},${{st.lon}}">${{st.name}}</option>`;
                    }});
                    select.disabled = false;
                }} catch (e) {{
                    select.innerHTML = '<option>Error loading stations</option>';
                }}
            }}
            
            async function routeByStation() {{
                const startVal = document.getElementById('start-station').value;
                const endVal = document.getElementById('end-station').value;
                
                if (!startVal || !endVal) {{
                    alert("Please select both a start and end station.");
                    return;
                }}
                
                const startCountry = document.getElementById('start-country-station').value;
                const endCountry = document.getElementById('end-country-station').value;
                
                const startCoords = startVal.split(',');
                const endCoords = endVal.split(',');
                
                const startLatLng = L.latLng(parseFloat(startCoords[0]), parseFloat(startCoords[1]));
                const endLatLng = L.latLng(parseFloat(endCoords[0]), parseFloat(endCoords[1]));
                
                clearMap();
                startMarker = L.marker(startLatLng).addTo(map).bindPopup("Start Station").openPopup();
                endMarker = L.marker(endLatLng).addTo(map).bindPopup("End Station").openPopup();
                
                // Pan map to fit both stations
                map.fitBounds(L.latLngBounds(startLatLng, endLatLng), {{padding: [50, 50]}});
                
                await triggerRouteApi(startCountry, endCountry, startLatLng, endLatLng);
            }}

            function clearMap() {{
                if (startMarker) map.removeLayer(startMarker);
                if (endMarker) map.removeLayer(endMarker);
                if (routeLayer) map.removeLayer(routeLayer);
                startMarker = null;
                endMarker = null;
                routeLayer = null;
                document.getElementById('results').classList.add('hidden');
            }}

            async function triggerRouteApi(startCountry, endCountry, startLL, endLL) {{
                document.getElementById('loading').classList.remove('hidden');
                document.getElementById('results').classList.add('hidden');
                
                const payload = {{
                    start_country: startCountry,
                    end_country: endCountry,
                    start_lat: startLL.lat,
                    start_lon: startLL.lng,
                    end_lat: endLL.lat,
                    end_lon: endLL.lng
                }};

                try {{
                    const response = await fetch('api/route', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(payload)
                    }});
                    
                    const data = await response.json();
                    document.getElementById('loading').classList.add('hidden');
                    
                    if (!response.ok) {{
                        alert("Routing failed: " + data.detail);
                        return;
                    }}

                    // Draw Line
                    const latlngs = data.coordinates.map(c => [c[1], c[0]]); // GeoJSON is [lon, lat], Leaflet wants [lat, lon]
                    
                    if (routeLayer) map.removeLayer(routeLayer);
                    routeLayer = L.polyline(latlngs, {{color: 'red', weight: 4}}).addTo(map);
                    
                    // Zoom to fit route
                    map.fitBounds(routeLayer.getBounds(), {{padding: [50, 50]}});
                    
                    document.getElementById('res-dist').innerText = data.distance_km.toFixed(2);
                    document.getElementById('res-nodes').innerText = data.node_count;
                    document.getElementById('results').classList.remove('hidden');
                    
                }} catch (error) {{
                    document.getElementById('loading').classList.add('hidden');
                    alert("Error communicating with server.");
                    console.error(error);
                }}
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# Global cache for stations to prevent slow re-parsing of massive GeoJSONs
station_cache = {}

@app.get("/api/stations")
def get_stations(country: str):
    if country in station_cache:
        return station_cache[country]
        
    geojson_path = base_dir / "data" / "processed" / "geojson" / country / f"{country}_stations.geojson"
    if not geojson_path.exists():
        return []
        
    try:
        gdf = gpd.read_file(geojson_path)
        stations = []
        
        # Find the name column — pyrosm may export it as 'name', 'tags.name', or other variants
        name_col = None
        for candidate in ['name', 'tags.name', 'Name', 'NAME']:
            if candidate in gdf.columns:
                name_col = candidate
                break
        
        if name_col is None:
            # No name column at all — use index as fallback
            for idx, row in gdf.iterrows():
                if row.geometry and row.geometry.geom_type == 'Point':
                    stations.append({
                        "name": f"Station #{idx}",
                        "lon": row.geometry.x,
                        "lat": row.geometry.y
                    })
        else:
            named_stations = gdf[gdf[name_col].notna() & (gdf[name_col] != '')]
            named_stations = named_stations.sort_values(name_col)
            
            for _, row in named_stations.iterrows():
                if row.geometry and row.geometry.geom_type == 'Point':
                    stations.append({
                        "name": str(row[name_col]),
                        "lon": row.geometry.x,
                        "lat": row.geometry.y
                    })
                
        station_cache[country] = stations
        return stations
    except Exception as e:
        print(f"Error loading stations: {e}")
        return []

@app.post("/api/route")
async def route(req: RouteRequest):
    global routers
    
    sc = req.start_country.lower()
    ec = req.end_country.lower()
    
    # Create a unique cache key for the country combination
    countries = sorted(list(set([sc, ec])))
    cache_key = ",".join(countries)
    
    # Lazy Load Router
    if cache_key not in routers:
        try:
            routers[cache_key] = RailwayRouter(countries)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to load graph for {cache_key}: {str(e)}")
            
    router = routers[cache_key]
    
    # Calculate path
    path_nodes, distance = router.route(req.start_lat, req.start_lon, req.end_lat, req.end_lon)
    
    if not path_nodes:
        raise HTTPException(status_code=404, detail="No physical track path found between these coordinates in the connected network.")
        
    # Convert node IDs to GeoJSON coordinates (LineString format: [lon, lat])
    coordinates = []
    for node_id in path_nodes:
        data = router.G.nodes[node_id]
        coordinates.append([float(data['x']), float(data['y'])])
        
    return {
        "status": "success",
        "distance_km": distance,
        "node_count": len(path_nodes),
        "coordinates": coordinates
    }

# ═══════════════════════════════════════════════════════════
# Phase 4: Simulation Dashboard Endpoints
# ═══════════════════════════════════════════════════════════

class SimStartRequest(BaseModel):
    country: str
    num_trains: int = 5
    speed_kmh: float = 120.0
    tick_seconds: int = 60

@app.post("/api/sim/start")
def start_simulation(req: SimStartRequest):
    global active_simulation, sim_thread, routers

    country = req.country.lower()

    # Load or reuse router
    if country not in routers:
        try:
            router = RailwayRouter(country)
            routers[country] = router
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    router = routers[country]
    add_route_from_nodes(router)

    # Load stations
    stations = get_stations(country)
    if len(stations) < 2:
        raise HTTPException(status_code=400, detail="Not enough stations for this country.")

    # Create simulation
    sim = RailwaySimulation(router, tick_seconds=req.tick_seconds)
    sim.spawn_random_trains(stations, count=req.num_trains, speed_kmh=req.speed_kmh)
    active_simulation = sim

    # Start auto-ticking in background
    sim.running = True
    def run_sim():
        import time as _time
        while sim.running:
            sim.tick()
            _time.sleep(0.5)

    sim_thread = threading.Thread(target=run_sim, daemon=True)
    sim_thread.start()

    return {"status": "started", "trains": len(sim.trains)}

@app.post("/api/sim/stop")
def stop_simulation():
    global active_simulation
    if active_simulation:
        active_simulation.running = False
    return {"status": "stopped"}

@app.post("/api/sim/reset")
def reset_simulation():
    global active_simulation
    if active_simulation:
        active_simulation.running = False
        active_simulation.reset()
    active_simulation = None
    return {"status": "reset"}

@app.get("/api/sim/state")
def get_sim_state():
    if not active_simulation:
        return {"running": False, "trains": [], "stats": {}}
    return {
        "running": active_simulation.running,
        "trains": active_simulation.get_train_positions(),
        "stats": active_simulation.get_stats()
    }

@app.get("/simulation", response_class=HTMLResponse)
def simulation_dashboard():
    country_options = "\n".join([f'<option value="{c}">{c.replace("_", " ").title()}</option>' for c in countries_list])

    html = """<!DOCTYPE html>
<html>
<head>
    <title>ARTEMIS - Train Simulation</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        body, html { margin: 0; padding: 0; height: 100%; font-family: sans-serif; background: #0f172a; color: #e2e8f0; }
        #map { height: 100vh; width: 100vw; }
        #panel {
            position: absolute; top: 20px; right: 20px; z-index: 1000;
            background: #1e293b; padding: 20px; border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.4); width: 340px;
            max-height: 90vh; overflow-y: auto;
        }
        h2 { margin-top: 0; color: #38bdf8; }
        label { font-size: 13px; font-weight: bold; color: #94a3b8; }
        select, input { width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box;
            background: #334155; border: 1px solid #475569; color: #e2e8f0; border-radius: 4px; }
        .btn { padding: 10px; border: none; border-radius: 6px; cursor: pointer;
            width: 100%; margin-top: 10px; font-weight: bold; font-size: 14px; }
        .btn-start { background: #22c55e; color: white; }
        .btn-start:hover { background: #16a34a; }
        .btn-stop { background: #ef4444; color: white; }
        .btn-stop:hover { background: #dc2626; }
        .btn-reset { background: #6366f1; color: white; }
        .btn-reset:hover { background: #4f46e5; }
        .stat-box { display: flex; justify-content: space-between; background: #334155;
            padding: 8px 12px; border-radius: 6px; margin-top: 6px; }
        .stat-label { color: #94a3b8; font-size: 12px; }
        .stat-value { color: #f1f5f9; font-weight: bold; font-size: 16px; }
        .train-list { max-height: 200px; overflow-y: auto; margin-top: 10px; }
        .train-item { background: #334155; padding: 6px 10px; border-radius: 4px; margin-top: 4px; font-size: 12px; }
    </style>
</head>
<body>
    <div id="panel">
        <h2>ARTEMIS Simulation</h2>

        <label>Country Network:</label>
        <select id="country">""" + country_options + """</select>

        <div style="display: flex; gap: 10px; margin-top: 10px;">
            <div style="flex: 1;"><label>Trains:</label><input id="num-trains" type="number" value="10" min="1" max="100"></div>
            <div style="flex: 1;"><label>Speed (km/h):</label><input id="speed" type="number" value="120" min="10" max="400"></div>
        </div>

        <button class="btn btn-start" onclick="startSim()">Start Simulation</button>
        <button class="btn btn-stop" onclick="stopSim()">Stop</button>
        <button class="btn btn-reset" onclick="resetSim()">Reset</button>

        <div style="margin-top: 15px;">
            <div class="stat-box">
                <div><div class="stat-label">Tick</div><div class="stat-value" id="s-tick">0</div></div>
                <div><div class="stat-label">Active</div><div class="stat-value" id="s-active" style="color:#22c55e;">0</div></div>
                <div><div class="stat-label">Blocked</div><div class="stat-value" id="s-blocked" style="color:#f59e0b;">0</div></div>
                <div><div class="stat-label">Arrived</div><div class="stat-value" id="s-arrived" style="color:#38bdf8;">0</div></div>
            </div>
        </div>

        <div class="train-list" id="train-list"></div>
    </div>
    <div id="map"></div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        const map = L.map('map').setView([53.5, -2.5], 6);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            attribution: 'OpenStreetMap CARTO'
        }).addTo(map);

        const countryCenters = {
            'argentina': [-38.4, -63.6], 'australia': [-25.2, 133.7], 'brazil': [-14.2, -51.9],
            'canada': [56.1, -106.3], 'china': [35.8, 104.1], 'france': [46.2, 2.2],
            'germany': [51.1, 10.4], 'italy': [41.8, 12.5], 'japan': [36.2, 138.2],
            'mexico': [23.6, -102.5], 'russia': [61.5, 105.3], 'south_africa': [-30.5, 22.9],
            'uk': [53.5, -2.5], 'us': [39.8, -98.5]
        };

        document.getElementById('country').addEventListener('change', e => {
            if (countryCenters[e.target.value]) map.setView(countryCenters[e.target.value], 5);
        });

        let trainMarkers = {};
        let pollInterval = null;

        function getIcon(status) {
            const emoji = status === 'BLOCKED' ? '🟡' : status === 'ARRIVED' ? '✅' : '🚆';
            return L.divIcon({className: '', html: '<div style="font-size:18px;">' + emoji + '</div>', iconSize: [20, 20], iconAnchor: [10, 10]});
        }

        async function startSim() {
            const country = document.getElementById('country').value;
            const numTrains = parseInt(document.getElementById('num-trains').value);
            const speed = parseFloat(document.getElementById('speed').value);

            if (countryCenters[country]) map.setView(countryCenters[country], 6);

            await fetch('api/sim/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ country: country, num_trains: numTrains, speed_kmh: speed, tick_seconds: 60 })
            });

            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(pollState, 500);
        }

        async function stopSim() {
            await fetch('api/sim/stop', { method: 'POST' });
            if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
        }

        async function resetSim() {
            await fetch('api/sim/reset', { method: 'POST' });
            if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
            Object.values(trainMarkers).forEach(m => map.removeLayer(m));
            trainMarkers = {};
            document.getElementById('train-list').innerHTML = '';
            document.getElementById('s-tick').innerText = '0';
            document.getElementById('s-active').innerText = '0';
            document.getElementById('s-blocked').innerText = '0';
            document.getElementById('s-arrived').innerText = '0';
        }

        async function pollState() {
            try {
                const res = await fetch('api/sim/state');
                const data = await res.json();

                if (data.stats) {
                    document.getElementById('s-tick').innerText = data.stats.tick || 0;
                    document.getElementById('s-active').innerText = data.stats.active || 0;
                    document.getElementById('s-blocked').innerText = data.stats.blocked || 0;
                    document.getElementById('s-arrived').innerText = data.stats.arrived || 0;
                }

                const seenIds = new Set();
                let listHtml = '';

                (data.trains || []).forEach(t => {
                    seenIds.add(t.id);
                    const latlng = L.latLng(t.lat, t.lon);

                    if (trainMarkers[t.id]) {
                        trainMarkers[t.id].setLatLng(latlng);
                        trainMarkers[t.id].setIcon(getIcon(t.status));
                    } else {
                        trainMarkers[t.id] = L.marker(latlng, { icon: getIcon(t.status) }).addTo(map);
                    }
                    trainMarkers[t.id].bindPopup(
                        '<b>' + t.id + '</b><br>' + t.origin + ' to ' + t.destination +
                        '<br>' + t.distance_km + ' / ' + t.total_km + ' km<br>Status: ' + t.status
                    );

                    const emoji = t.status === 'EN_ROUTE' ? '🟢' : t.status === 'BLOCKED' ? '🟡' : '🔵';
                    listHtml += '<div class="train-item">' + emoji + ' ' + t.id + ': ' +
                        t.origin.substring(0,18) + ' → ' + t.destination.substring(0,18) +
                        ' (' + t.progress + '%)</div>';
                });

                document.getElementById('train-list').innerHTML = listHtml;

                Object.keys(trainMarkers).forEach(id => {
                    if (!seenIds.has(id)) { map.removeLayer(trainMarkers[id]); delete trainMarkers[id]; }
                });

                if (data.stats && data.stats.arrived === data.stats.total_trains && data.stats.total_trains > 0) {
                    clearInterval(pollInterval); pollInterval = null;
                }
            } catch(e) { console.error('Poll error:', e); }
        }
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)

if __name__ == "__main__":
    import uvicorn
    print("="*60)
    print("Starting ARTEMIS Server (Routing + Simulation)...")
    print("Routing UI:    http://127.0.0.1:8000/")
    print("Simulation:    http://127.0.0.1:8000/simulation")
    print("="*60)
    uvicorn.run("07_visualization_server:app", host="0.0.0.0", port=8000, reload=True)
