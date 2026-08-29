#!/usr/bin/env python3
"""
ARTEMIS - RL Simulation Dashboard
==================================
A FastAPI backend serving:
1. Interactive Train Dispatcher UI (station selection, train queuing)
2. Real-time RL simulation with live Leaflet.js map visualization
3. Station API for UK railway stations
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
from typing import List
import geopandas as gpd

# Import our router from 06_spatial_routing
base_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(base_dir / "scripts"))
sys.path.append(str(base_dir)) # Add root to path for versions module
try:
    from importlib import import_module
    routing_module = import_module("06_spatial_routing")
    RailwayRouter = routing_module.RailwayRouter


    rl_module = import_module("versions.v2.scripts.rl_sim_controller")
    RLSimController = rl_module.RLSimController
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
rl_active_simulation = None
station_cache = {}

@app.get("/api/stations")
def get_stations(country: str = "uk"):
    if country in station_cache:
        return station_cache[country]
        
    geojson_path = base_dir / "data" / "processed" / "geojson" / country / f"{country}_stations.geojson"
    if not geojson_path.exists():
        return []
        
    try:
        gdf = gpd.read_file(geojson_path)
        stations = []
        
        name_col = None
        for candidate in ['name', 'tags.name', 'Name', 'NAME']:
            if candidate in gdf.columns:
                name_col = candidate
                break
        
        if name_col is None:
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

# ═══════════════════════════════════════════════════════════
# Simulation Dashboard Endpoints
# ═══════════════════════════════════════════════════════════

class TrainConfig(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float

class SimStartRequest(BaseModel):
    country: str = "uk"
    trains: List[TrainConfig]
    speed_kmh: float = 120.0
    tick_seconds: int = 60

@app.post("/api/rl_sim/start")
def start_rl_simulation(req: SimStartRequest):
    global rl_active_simulation
    if rl_active_simulation and rl_active_simulation.running:
        return {"status": "already_running"}
        
    country = req.country.lower()
    train_configs = [t.dict() for t in req.trains]
    rl_active_simulation = RLSimController(country=country, trains=train_configs)
    rl_active_simulation.start()
    return {"status": "started", "trains": rl_active_simulation.num_agents}

@app.post("/api/rl_sim/stop")
def stop_rl_simulation():
    global rl_active_simulation
    if rl_active_simulation:
        rl_active_simulation.stop()
        rl_active_simulation = None
    return {"status": "stopped"}

@app.get("/api/rl_sim/state")
def get_rl_sim_state():
    if not rl_active_simulation:
        return {"running": False, "trains": [], "stats": {}}
    return rl_active_simulation.get_state()

@app.get("/", response_class=HTMLResponse)
def simulation_dashboard():
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
        <h2>ARTEMIS Train Dispatcher</h2>

        <div style="background: #334155; padding: 10px; border-radius: 6px; margin-bottom: 15px;">
            <label>Start Station:</label>
            <select id="start-station"><option>Loading...</option></select>
            <label>End Station:</label>
            <select id="end-station"><option>Loading...</option></select>
            <button class="btn" style="background:#0ea5e9; color:white;" onclick="addTrain()">Add Train</button>
        </div>

        <div id="pending-trains" style="margin-bottom:15px; font-size:12px; color:#cbd5e1; max-height:100px; overflow-y:auto;"></div>

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

        let trainMarkers = {};
        let pollInterval = null;
        let pendingTrains = [];

        async function loadStations() {
            try {
                const res = await fetch('api/stations?country=uk');
                const stations = await res.json();
                let opts = '<option value="">Select a station...</option>';
                stations.forEach(s => {
                    opts += `<option value="${s.lat},${s.lon}">${s.name}</option>`;
                });
                document.getElementById('start-station').innerHTML = opts;
                document.getElementById('end-station').innerHTML = opts;
            } catch(e) {
                console.error("Failed to load stations", e);
            }
        }
        
        loadStations();

        function addTrain() {
            const startVal = document.getElementById('start-station').value;
            const endVal = document.getElementById('end-station').value;
            if (!startVal || !endVal) return alert("Select both start and end stations");
            
            const [slat, slon] = startVal.split(',').map(Number);
            const [elat, elon] = endVal.split(',').map(Number);
            
            const startName = document.getElementById('start-station').options[document.getElementById('start-station').selectedIndex].text;
            const endName = document.getElementById('end-station').options[document.getElementById('end-station').selectedIndex].text;
            
            pendingTrains.push({
                start_lat: slat, start_lon: slon, end_lat: elat, end_lon: elon,
                name: `${startName} → ${endName}`
            });
            updatePendingUI();
        }

        function updatePendingUI() {
            let html = '<b>Pending Trains:</b><br>';
            pendingTrains.forEach((t, i) => {
                html += `<div style="margin-top:4px;">🚆 T${i}: ${t.name}</div>`;
            });
            document.getElementById('pending-trains').innerHTML = html;
        }

        function getIcon(status) {
            const emoji = status === 'BLOCKED' ? '🟡' : status === 'ARRIVED' ? '✅' : '🚆';
            return L.divIcon({className: '', html: '<div style="font-size:18px;">' + emoji + '</div>', iconSize: [20, 20], iconAnchor: [10, 10]});
        }

        async function startSim() {
            if (pendingTrains.length === 0) return alert("Please add at least one train!");
            
            const payload = {
                country: 'uk',
                trains: pendingTrains,
                speed_kmh: 120.0,
                tick_seconds: 60
            };

            await fetch('api/rl_sim/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(pollState, 500);
        }

        async function stopSim() {
            await fetch('api/rl_sim/stop', { method: 'POST' });
            if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
        }

        async function resetSim() {
            await fetch('api/rl_sim/stop', { method: 'POST' });
            
            if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
            Object.values(trainMarkers).forEach(m => map.removeLayer(m));
            trainMarkers = {};
            pendingTrains = [];
            updatePendingUI();
            document.getElementById('train-list').innerHTML = '';
            document.getElementById('s-tick').innerText = '0';
            document.getElementById('s-active').innerText = '0';
            document.getElementById('s-blocked').innerText = '0';
            document.getElementById('s-arrived').innerText = '0';
        }

        async function pollState() {
            try {
                const res = await fetch('api/rl_sim/state');
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
    print("Starting ARTEMIS Server (Simulation Only)...")
    print("Simulation:    http://127.0.0.1:8000/")
    print("="*60)
    uvicorn.run("07_visualization_server:app", host="0.0.0.0", port=8000, reload=True)
