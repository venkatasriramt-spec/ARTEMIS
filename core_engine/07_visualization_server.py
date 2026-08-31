#!/usr/bin/env python3
"""
ARTEMIS - UK Railway Network Visualizer
========================================
Single-page web app that renders the entire UK railway network on a map.
Tracks with active trains are highlighted in a distinct color.
"""

import os
import sys
import json
import threading
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import geopandas as gpd
import networkx as nx
from dotenv import load_dotenv

load_dotenv()

# Imports
base_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(base_dir / "scripts"))
sys.path.append(str(base_dir))
try:
    from importlib import import_module
    routing_module = import_module("06_spatial_routing")
    RailwayRouter = routing_module.RailwayRouter
    rl_module = import_module("versions.v2.scripts.rl_sim_controller")
    RLSimController = rl_module.RLSimController
except ImportError as e:
    print(f"Error: Could not import modules: {e}")
    sys.exit(1)

app = FastAPI(title="ARTEMIS UK Railway Network")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── State ──
rl_sim = None
station_cache = None
network_geojson_cache = None

# ═══════════════════════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════════════════════

@app.get("/api/stations")
def get_stations():
    global station_cache
    if station_cache is not None:
        return station_cache

    geojson_path = base_dir / "data" / "processed" / "geojson" / "uk" / "uk_stations.geojson"
    if not geojson_path.exists():
        return []
    
    gdf = gpd.read_file(geojson_path)
    stations = []
    name_col = None
    for c in ['name', 'tags.name', 'Name', 'NAME']:
        if c in gdf.columns:
            name_col = c
            break
    
    if name_col:
        named = gdf[gdf[name_col].notna() & (gdf[name_col] != '')].sort_values(name_col)
        for _, row in named.iterrows():
            if row.geometry and row.geometry.geom_type == 'Point':
                stations.append({"name": str(row[name_col]), "lon": row.geometry.x, "lat": row.geometry.y})
    else:
        for idx, row in gdf.iterrows():
            if row.geometry and row.geometry.geom_type == 'Point':
                stations.append({"name": f"Station #{idx}", "lon": row.geometry.x, "lat": row.geometry.y})
    
    station_cache = stations
    return stations

@app.get("/api/network")
def get_network():
    """Returns the continuous UK railway tracks as GeoJSON."""
    global network_geojson_cache
    if network_geojson_cache is not None:
        return JSONResponse(content=network_geojson_cache)

    geojson_path = base_dir / "data" / "processed" / "geojson" / "uk" / "uk_network_lines.geojson"
    if not geojson_path.exists():
        return JSONResponse(content={"type": "FeatureCollection", "features": []})

    print("Loading UK continuous railway network for visualization...")
    with open(geojson_path, 'r') as f:
        geojson = json.load(f)

    network_geojson_cache = geojson
    print(f"Network ready: {len(geojson['features']):,} continuous track segments.")
    return JSONResponse(content=geojson)

class TrainConfig(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float

class AddTrainsRequest(BaseModel):
    trains: List[TrainConfig]

@app.post("/api/trains/add")
def add_trains(req: AddTrainsRequest):
    global rl_sim
    configs = [t.dict() for t in req.trains]

    if rl_sim is None or not rl_sim.running:
        # First train(s) — start the simulation
        rl_sim = RLSimController(country="uk", trains=configs)
        rl_sim.start()
        return {"status": "started", "count": len(configs)}
    else:
        # Hot-add to running simulation
        rl_sim.add_trains(configs)
        return {"status": "added", "count": len(configs)}

@app.post("/api/trains/clear")
def clear_trains():
    global rl_sim
    if rl_sim:
        rl_sim.stop()
        rl_sim = None
    return {"status": "cleared"}

@app.get("/api/trains/state")
def get_train_state():
    if not rl_sim or not rl_sim.running:
        return {"trains": [], "active_edges": []}

    state = rl_sim.get_state()
    raw_edges = rl_sim.get_active_edges()
    env = rl_sim.env

    edge_coords = []
    for u, v in raw_edges:
        try:
            ud, vd = env.router.G.nodes[u], env.router.G.nodes[v]
            edge_coords.append([[float(ud['x']), float(ud['y'])], [float(vd['x']), float(vd['y'])]])
        except (KeyError, ValueError):
            continue

    # Build the full path for each active train as coordinate arrays
    train_paths = []
    for i in range(env.num_agents):
        if not env.reached_destination[i]:
            path = env.optimal_paths[i]
            coords = []
            # Sample the path densely so it hugs the curved track geometry perfectly
            step = max(1, len(path) // 2000)
            for j in range(0, len(path), step):
                nd = env.router.G.nodes[path[j]]
                coords.append([float(nd['x']), float(nd['y'])])
            if coords:
                train_paths.append(coords)

    return {
        "trains": state.get("trains", []),
        "stats": state.get("stats", {}),
        "active_edges": edge_coords,
        "train_paths": train_paths
    }

# ═══════════════════════════════════════════════════════════
# The One Page
# ═══════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def index():
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    html_content = PAGE_HTML.replace("YOUR_API_KEY_HERE", api_key)
    return HTMLResponse(content=html_content)

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>ARTEMIS — UK Railway Network</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="ARTEMIS — Live visualization of the entire UK railway network powered by Reinforcement Learning">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script async defer src="https://maps.googleapis.com/maps/api/js?key=YOUR_API_KEY_HERE&callback=initMap"></script>
    <style>
        :root {
            --bg-deep: #f8fafc;
            --bg-panel: rgba(255, 255, 255, 0.95);
            --bg-card: rgba(241, 245, 249, 0.85);
            --border: rgba(148, 163, 184, 0.2);
            --border-hover: rgba(37, 99, 235, 0.5);
            --text: #334155;
            --text-dim: #64748b;
            --text-bright: #0f172a;
            --accent: #2563eb;
            --accent-glow: rgba(37, 99, 235, 0.15);
            --track-idle: #94a3b8;
            --track-active: #ea580c;
            --track-path: #3b82f6;
            --green: #16a34a;
            --red: #dc2626;
        }
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body, html { height: 100%; width: 100%; font-family: 'Inter', -apple-system, sans-serif; background: var(--bg-deep); color: var(--text); overflow: hidden; }
        #map { position: fixed; inset: 0; z-index: 1; width: 100%; height: 100%; }

        /* ── Top bar ── */
        #topbar {
            position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
            display: flex; align-items: center; justify-content: space-between;
            padding: 12px 24px;
            background: linear-gradient(180deg, rgba(255,255,255,0.95) 0%, rgba(255,255,255,0.7) 80%, transparent 100%);
            pointer-events: none;
        }
        #topbar > * { pointer-events: auto; }
        .logo { display: flex; align-items: center; gap: 10px; }
        .logo-icon { font-size: 22px; }
        .logo-text { font-size: 16px; font-weight: 700; color: var(--text-bright); letter-spacing: -0.5px; }
        .logo-sub { font-size: 11px; color: var(--text-dim); font-weight: 400; margin-left: 2px; }
        .stats-bar { display: flex; gap: 20px; align-items: center; }
        .stat { text-align: center; }
        .stat-val { font-size: 18px; font-weight: 700; color: var(--text-bright); }
        .stat-lbl { font-size: 9px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); margin-top: 1px; }

        /* ── Side panel ── */
        #panel {
            position: fixed; top: 60px; right: 16px; bottom: 16px; z-index: 1000;
            width: 320px; background: var(--bg-panel);
            border-radius: 16px; border: 1px solid var(--border);
            backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
            display: flex; flex-direction: column;
            box-shadow: 0 16px 64px rgba(0,0,0,0.6);
            overflow: hidden;
            transition: transform 0.3s ease;
        }
        .panel-header { padding: 16px 18px 12px; border-bottom: 1px solid var(--border); }
        .panel-header h3 { font-size: 13px; font-weight: 600; color: var(--accent); text-transform: uppercase; letter-spacing: 1px; }
        .panel-body { flex: 1; overflow-y: auto; padding: 14px 18px; }
        .panel-footer { padding: 12px 18px; border-top: 1px solid var(--border); }

        /* ── Station Info Panel ── */
        #station-panel {
            display: none; padding: 14px 18px; background: rgba(37, 99, 235, 0.05);
            border-bottom: 1px solid var(--border);
            animation: fadeIn 0.3s ease;
        }
        .stn-title { font-size: 14px; font-weight: 700; color: var(--text-bright); margin-bottom: 4px; }
        .stn-coords { font-size: 10px; color: var(--text-dim); font-family: monospace; margin-bottom: 12px; }
        .stn-btn-group { display: flex; gap: 8px; }
        .btn-sm { flex: 1; padding: 6px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 6px; }

        label { display: block; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; color: var(--text-dim); margin-top: 12px; margin-bottom: 4px; }
        label:first-child { margin-top: 0; }
        select {
            width: 100%; padding: 9px 10px; background: var(--bg-deep); border: 1px solid var(--border);
            color: var(--text); border-radius: 8px; font-family: 'Inter', sans-serif; font-size: 12px;
            appearance: none; cursor: pointer; transition: border-color 0.2s;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%235a6e82'/%3E%3C/svg%3E");
            background-repeat: no-repeat; background-position: right 10px center;
        }
        select:focus { outline: none; border-color: var(--accent); }

        .btn {
            width: 100%; padding: 10px; border: none; border-radius: 8px; cursor: pointer;
            font-family: 'Inter', sans-serif; font-size: 12px; font-weight: 600;
            letter-spacing: 0.3px; transition: all 0.15s ease;
        }
        .btn:active { transform: scale(0.97); }
        .btn-add { background: linear-gradient(135deg, #0ea5e9, #0369a1); color: white; margin-top: 14px; }
        .btn-add:hover { background: linear-gradient(135deg, #38bdf8, #0ea5e9); box-shadow: 0 4px 16px rgba(14, 165, 233, 0.3); }
        .btn-clear { background: transparent; color: var(--text-dim); border: 1px solid var(--border); font-size: 11px; }
        .btn-clear:hover { border-color: var(--red); color: var(--red); }

        .train-queue { margin-top: 16px; }
        .train-queue-title { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; color: var(--text-dim); margin-bottom: 6px; }
        
        .train-details {
            background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
            margin-bottom: 4px; overflow: hidden;
            animation: fadeIn 0.3s ease;
        }
        .train-details[open] { border-color: var(--accent); }
        .train-details summary {
            display: flex; align-items: center; gap: 8px; padding: 8px 10px;
            font-size: 11px; color: var(--text); cursor: pointer; list-style: none;
        }
        .train-details summary::-webkit-details-marker { display: none; }
        .train-details summary:hover { background: rgba(0,0,0,0.02); }
        .queue-idx { font-weight: 700; color: var(--accent); min-width: 18px; }
        .queue-route { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .queue-status { font-size: 14px; }
        .queue-pct { font-size: 10px; color: var(--text-dim); min-width: 32px; text-align: right; }
        
        .train-meta {
            padding: 8px 10px 10px; font-size: 10px; color: var(--text-dim);
            border-top: 1px solid var(--border); background: var(--bg-deep);
            display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
        }
        .train-meta span { color: var(--text-bright); font-weight: 600; }
        
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }

        /* ── Legend ── */
        .legend { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); }
        .legend-item { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 11px; color: var(--text-dim); }
        .legend-line { width: 24px; height: 3px; border-radius: 2px; }

        /* ── Loading overlay ── */
        #loading {
            position: fixed; inset: 0; z-index: 9999;
            background: var(--bg-deep);
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            transition: opacity 0.6s ease;
        }
        #loading.hidden { opacity: 0; pointer-events: none; }
        .spinner { width: 40px; height: 40px; border: 3px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .loading-text { margin-top: 16px; font-size: 13px; color: var(--text-dim); }
        .loading-sub { margin-top: 4px; font-size: 11px; color: var(--text-dim); opacity: 0.5; }

        /* Custom Google Maps Marker Label */
        .gmap-marker-label {
            font-size: 18px;
            filter: drop-shadow(0 0 4px rgba(56,189,248,0.8));
            transform: translateY(-50%);
        }

        /* ── Scrollbar ── */
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
    </style>
</head>
<body>

<div id="loading">
    <div class="spinner"></div>
    <div class="loading-text">Loading UK Railway Network</div>
    <div class="loading-sub">608,000+ nodes · 1,200,000+ edges</div>
</div>

<div id="topbar">
    <div class="logo">
        <span class="logo-icon">🚄</span>
        <span class="logo-text">ARTEMIS <span class="logo-sub">UK Railway Network</span></span>
    </div>
    <div class="stats-bar">
        <div class="stat"><div class="stat-val" id="s-segments">—</div><div class="stat-lbl">Track Segments</div></div>
        <div class="stat"><div class="stat-val" id="s-trains" style="color:var(--accent);">0</div><div class="stat-lbl">Active Trains</div></div>
        <div class="stat"><div class="stat-val" id="s-arrived" style="color:var(--green);">0</div><div class="stat-lbl">Arrived</div></div>
    </div>
</div>

<div id="panel">
    <div class="panel-header"><h3>Train Dispatcher</h3></div>
    <div id="station-panel">
        <div class="stn-title" id="sp-name">Station Name</div>
        <div class="stn-coords" id="sp-coords">54.52, -3.21</div>
        <div class="stn-btn-group">
            <button class="btn btn-add btn-sm" onclick="setStation('start')">Set as Origin</button>
            <button class="btn btn-add btn-sm" onclick="setStation('end')">Set as Dest</button>
        </div>
    </div>
    <div class="panel-body">
        <label>Origin Station</label>
        <select id="sel-start"><option>Loading stations...</option></select>
        <label>Destination Station</label>
        <select id="sel-end"><option>Loading stations...</option></select>
        <button class="btn btn-add" onclick="dispatchTrain()">Deploy Train</button>

        <div class="train-queue" id="train-queue"></div>

        <div class="legend">
            <div class="legend-item"><div class="legend-line" style="background:var(--track-idle);"></div> Idle Track</div>
            <div class="legend-item"><div class="legend-line" style="background:var(--track-path);"></div> Train Route</div>
            <div class="legend-item"><div class="legend-line" style="background:var(--track-active);"></div> Active Segment</div>
        </div>
    </div>
    <div class="panel-footer">
        <button class="btn btn-clear" onclick="clearAll()">Clear All Trains</button>
    </div>
</div>

<div id="map"></div>

<script>
let map;
let activePolylines = [];
let pathPolylines = [];
let trainMarkers = {};
let pollTimer = null;

// Initialize Google Map
function initMap() {
    // Professional silver/light mode style
    const lightStyle = [
      { elementType: "geometry", stylers: [{ color: "#f5f5f5" }] },
      { elementType: "labels.icon", stylers: [{ visibility: "off" }] },
      { elementType: "labels.text.fill", stylers: [{ color: "#616161" }] },
      { elementType: "labels.text.stroke", stylers: [{ color: "#f5f5f5" }] },
      { featureType: "administrative.land_parcel", elementType: "labels.text.fill", stylers: [{ color: "#bdbdbd" }] },
      { featureType: "poi", elementType: "geometry", stylers: [{ color: "#eeeeee" }] },
      { featureType: "poi", elementType: "labels.text.fill", stylers: [{ color: "#757575" }] },
      { featureType: "poi.park", elementType: "geometry", stylers: [{ color: "#e5e5e5" }] },
      { featureType: "poi.park", elementType: "labels.text.fill", stylers: [{ color: "#9e9e9e" }] },
      { featureType: "road", elementType: "geometry", stylers: [{ color: "#ffffff" }] },
      { featureType: "road.arterial", elementType: "labels.text.fill", stylers: [{ color: "#757575" }] },
      { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#dadada" }] },
      { featureType: "road.highway", elementType: "labels.text.fill", stylers: [{ color: "#616161" }] },
      { featureType: "road.local", elementType: "labels.text.fill", stylers: [{ color: "#9e9e9e" }] },
      { featureType: "transit.line", elementType: "geometry", stylers: [{ color: "#e5e5e5" }] },
      { featureType: "transit.station", elementType: "geometry", stylers: [{ color: "#eeeeee" }] },
      { featureType: "water", elementType: "geometry", stylers: [{ color: "#c9c9c9" }] },
      { featureType: "water", elementType: "labels.text.fill", stylers: [{ color: "#9e9e9e" }] }
    ];

    map = new google.maps.Map(document.getElementById('map'), {
        center: { lat: 54.5, lng: -3.5 },
        zoom: 6,
        styles: lightStyle,
        disableDefaultUI: true,
        zoomControl: true,
        zoomControlOptions: { position: google.maps.ControlPosition.LEFT_BOTTOM }
    });

    loadNetwork();
    loadStations();
}

// ── Load Network ──
async function loadNetwork() {
    try {
        const res = await fetch('api/network');
        const geojson = await res.json();
        
        map.data.addGeoJson(geojson);
        map.data.setStyle({
            strokeColor: '#94a3b8',
            strokeWeight: 1.2,
            strokeOpacity: 0.6
        });
        
        document.getElementById('s-segments').textContent = geojson.features.length.toLocaleString();
        document.getElementById('loading').classList.add('hidden');
    } catch(e) {
        document.querySelector('.loading-text').textContent = 'Failed to load network';
        console.error(e);
    }
}

// ── Load Stations ──
async function loadStations() {
    try {
        const res = await fetch('api/stations');
        const stations = await res.json();
        let html = '<option value="">Choose station…</option>';
        stations.forEach(s => { 
            html += `<option value="${s.lat},${s.lon}">${s.name}</option>`; 
        });
        document.getElementById('sel-start').innerHTML = html;
        document.getElementById('sel-end').innerHTML = html;
    } catch(e) { console.error(e); }
}

let activeStation = null;
function showStationSidebar(stn) {
    activeStation = stn;
    document.getElementById('station-panel').style.display = 'block';
    document.getElementById('sp-name').textContent = stn.name;
    document.getElementById('sp-coords').textContent = `${stn.lat.toFixed(4)}, ${stn.lon.toFixed(4)}`;
}

function setStation(type) {
    if (!activeStation) return;
    const val = `${activeStation.lat},${activeStation.lon}`;
    document.getElementById(type === 'start' ? 'sel-start' : 'sel-end').value = val;
}

// ── Dispatch ──
async function dispatchTrain() {
    const sv = document.getElementById('sel-start').value;
    const ev = document.getElementById('sel-end').value;
    if (!sv || !ev) return alert('Select both origin and destination');
    if (sv === ev) return alert('Origin and destination must differ');

    const [slat, slon] = sv.split(',').map(Number);
    const [elat, elon] = ev.split(',').map(Number);

    await fetch('api/trains/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trains: [{ start_lat: slat, start_lon: slon, end_lat: elat, end_lon: elon }] })
    });

    if (!pollTimer) pollTimer = setInterval(pollState, 600);
}

async function clearAll() {
    await fetch('api/trains/clear', { method: 'POST' });
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    
    activePolylines.forEach(p => p.setMap(null));
    activePolylines = [];
    
    pathPolylines.forEach(p => p.setMap(null));
    pathPolylines = [];
    
    Object.values(trainMarkers).forEach(m => m.setMap(null));
    trainMarkers = {};
    
    document.getElementById('train-queue').innerHTML = '';
    document.getElementById('s-trains').textContent = '0';
    document.getElementById('s-arrived').textContent = '0';
}

// ── Poll ──
async function pollState() {
    try {
        const res = await fetch('api/trains/state');
        const data = await res.json();
        const trains = data.trains || [];
        const stats = data.stats || {};

        document.getElementById('s-trains').textContent = stats.active || 0;
        document.getElementById('s-arrived').textContent = stats.arrived || 0;

        // Clear previous overlays
        activePolylines.forEach(p => p.setMap(null));
        activePolylines = [];
        pathPolylines.forEach(p => p.setMap(null));
        pathPolylines = [];

        // Active edges
        if (data.active_edges && data.active_edges.length > 0) {
            data.active_edges.forEach(edge => {
                const poly = new google.maps.Polyline({
                    path: edge.map(c => ({ lat: c[1], lng: c[0] })),
                    strokeColor: '#ea580c',
                    strokeWeight: 4,
                    strokeOpacity: 1.0,
                    map: map
                });
                activePolylines.push(poly);
            });
        }

        // Paths
        if (data.train_paths && data.train_paths.length > 0) {
            data.train_paths.forEach(coords => {
                const poly = new google.maps.Polyline({
                    path: coords.map(c => ({ lat: c[1], lng: c[0] })),
                    strokeColor: '#3b82f6',
                    strokeWeight: 2.5,
                    strokeOpacity: 0.8,
                    map: map
                });
                pathPolylines.push(poly);
            });
        }

        // Train markers
        const seen = new Set();
        
        const queueContainer = document.getElementById('train-queue');
        if (trains.length === 0) {
            queueContainer.innerHTML = '';
        } else {
            let title = queueContainer.querySelector('.train-queue-title');
            if (!title) {
                title = document.createElement('div');
                title.className = 'train-queue-title';
                queueContainer.appendChild(title);
            }
            title.textContent = `Active Trains (${trains.length})`;
            
            trains.forEach((t, idx) => {
                seen.add(t.id);
                const pos = { lat: t.lat, lng: t.lon };
                const iconStr = t.status === 'ARRIVED' ? '✅' : '🚆';
                
                if (trainMarkers[t.id]) {
                    trainMarkers[t.id].setPosition(pos);
                    trainMarkers[t.id].setLabel({ text: iconStr, className: 'gmap-marker-label' });
                } else {
                    trainMarkers[t.id] = new google.maps.Marker({
                        position: pos,
                        map: map,
                        icon: { path: google.maps.SymbolPath.CIRCLE, scale: 0 },
                        label: { text: iconStr, className: 'gmap-marker-label' },
                        title: `T${idx} | Speed: ${t.speed_kmh} km/h | Progress: ${t.progress}%`
                    });
                }
                
                const statusIcon = t.status === 'ARRIVED' ? '✅' : '🟢';
                let el = document.getElementById(`td-${t.id}`);
                if (!el) {
                    el = document.createElement('details');
                    el.className = 'train-details';
                    el.id = `td-${t.id}`;
                    el.innerHTML = `
                        <summary>
                            <span class="queue-idx"></span>
                            <span class="queue-status"></span>
                            <span class="queue-route"></span>
                            <span class="queue-pct"></span>
                        </summary>
                        <div class="train-meta">
                            <div class="tm-speed">Speed: <span></span></div>
                            <div class="tm-dist">Distance: <span></span></div>
                            <div class="tm-start">Start: <span></span></div>
                            <div class="tm-stop">Arrival: <span></span></div>
                            <div style="grid-column: 1 / -1; margin-top:4px;">
                                <div class="tm-origin">Origin: <span></span></div>
                                <div class="tm-dest">Dest: <span></span></div>
                            </div>
                        </div>
                    `;
                    queueContainer.appendChild(el);
                }
                
                el.querySelector('.queue-idx').textContent = `T${idx}`;
                el.querySelector('.queue-status').textContent = statusIcon;
                el.querySelector('.queue-route').textContent = `${t.origin?.substring(0,12) || '?'} → ${t.destination?.substring(0,12) || '?'}`;
                el.querySelector('.queue-pct').textContent = `${t.progress}%`;
                
                el.querySelector('.tm-speed span').textContent = `${t.speed_kmh} km/h`;
                el.querySelector('.tm-dist span').textContent = `${t.distance_km} km`;
                el.querySelector('.tm-start span').textContent = t.start_time || '--';
                el.querySelector('.tm-stop span').textContent = t.stop_time || '--';
                el.querySelector('.tm-origin span').textContent = t.origin || 'Unknown';
                el.querySelector('.tm-dest span').textContent = t.destination || 'Unknown';
            });
            
            // Remove old details
            Array.from(queueContainer.querySelectorAll('.train-details')).forEach(el => {
                if (!seen.has(el.id.replace('td-', ''))) el.remove();
            });
        }

        Object.keys(trainMarkers).forEach(id => {
            if (!seen.has(id)) { trainMarkers[id].setMap(null); delete trainMarkers[id]; }
        });
    } catch(e) { /* ignore */ }
}
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  ARTEMIS — UK Railway Network Visualizer")
    print("  http://127.0.0.1:8000/")
    print("=" * 60)
    uvicorn.run("07_visualization_server:app", host="0.0.0.0", port=8000, reload=True)
