#!/usr/bin/env python3
"""
ARTEMIS - Phase 4: Multi-Agent Train Simulation Engine
======================================================
Discrete-event simulation where autonomous train agents navigate
the physical railway graph, respecting track capacity and avoiding
collisions.
"""

import os
import sys
import json
import time
import random
import logging
import threading
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module
routing_module = import_module("06_spatial_routing")
RailwayRouter = routing_module.RailwayRouter
haversine = routing_module.haversine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TrainStatus(str, Enum):
    IDLE = "IDLE"
    EN_ROUTE = "EN_ROUTE"
    BLOCKED = "BLOCKED"
    ARRIVED = "ARRIVED"


@dataclass
class TrainAgent:
    """An autonomous train agent that navigates the railway graph."""
    train_id: str
    origin_name: str
    destination_name: str
    origin_node: str
    destination_node: str
    route: List[str]              # Pre-computed A* path (list of node IDs)
    speed_kmh: float = 120.0      # Default speed in km/h
    status: TrainStatus = TrainStatus.IDLE
    route_index: int = 0          # Current position along the route
    distance_traveled: float = 0.0
    total_route_distance: float = 0.0
    blocked_ticks: int = 0
    elapsed_ticks: int = 0

    @property
    def current_node(self) -> str:
        if self.route_index < len(self.route):
            return self.route[self.route_index]
        return self.route[-1]

    @property
    def next_node(self) -> Optional[str]:
        if self.route_index + 1 < len(self.route):
            return self.route[self.route_index + 1]
        return None

    @property
    def progress_pct(self) -> float:
        if len(self.route) <= 1:
            return 100.0
        return (self.route_index / (len(self.route) - 1)) * 100.0


class RailwaySimulation:
    """
    Discrete-event simulation controller for multi-agent train routing.
    """
    def __init__(self, router: RailwayRouter, tick_seconds: int = 60):
        self.router = router
        self.tick_seconds = tick_seconds  # Simulated seconds per tick
        self.tick_count = 0
        self.trains: Dict[str, TrainAgent] = {}
        self.edge_occupancy: Dict[str, str] = {}  # "u->v" : train_id
        self.running = False
        self.history: List[dict] = []             # Stats per tick
        self._lock = threading.Lock()
        self._train_counter = 0

    def spawn_train(self, origin_node: str, dest_node: str,
                    origin_name: str = "", dest_name: str = "",
                    speed_kmh: float = 120.0) -> Optional[TrainAgent]:
        """Spawn a new train with a pre-computed A* route."""
        with self._lock:
            self._train_counter += 1
            train_id = f"T{self._train_counter:04d}"

        # Compute A* route using Phase 3 engine
        try:
            path = self.router.route_from_nodes(origin_node, dest_node)
        except Exception:
            path = None

        if not path:
            logger.warning(f"[{train_id}] No route from {origin_name} to {dest_name}. Skipping.")
            return None

        # Calculate total route distance
        total_dist = 0.0
        for i in range(len(path) - 1):
            u_data = self.router.G.nodes[path[i]]
            v_data = self.router.G.nodes[path[i + 1]]
            total_dist += haversine(
                float(u_data['x']), float(u_data['y']),
                float(v_data['x']), float(v_data['y'])
            )

        train = TrainAgent(
            train_id=train_id,
            origin_name=origin_name or origin_node,
            destination_name=dest_name or dest_node,
            origin_node=origin_node,
            destination_node=dest_node,
            route=path,
            speed_kmh=speed_kmh,
            status=TrainStatus.EN_ROUTE,
            total_route_distance=total_dist,
        )

        with self._lock:
            self.trains[train_id] = train

        logger.info(f"[{train_id}] Spawned: {origin_name} → {dest_name} "
                     f"({len(path)} nodes, {total_dist:.1f} km)")
        return train

    def spawn_random_trains(self, stations: List[dict], count: int = 5,
                            speed_kmh: float = 120.0):
        """Spawn N trains between random station pairs."""
        if len(stations) < 2:
            logger.error("Need at least 2 stations to spawn trains.")
            return

        spawned = 0
        attempts = 0
        while spawned < count and attempts < count * 5:
            attempts += 1
            origin, dest = random.sample(stations, 2)

            # Snap station coords to nearest graph node
            o_node, _, _ = self.router.find_nearest_node(origin['lat'], origin['lon'])
            d_node, _, _ = self.router.find_nearest_node(dest['lat'], dest['lon'])

            if o_node == d_node:
                continue

            train = self.spawn_train(
                origin_node=o_node, dest_node=d_node,
                origin_name=origin['name'], dest_name=dest['name'],
                speed_kmh=speed_kmh,
            )
            if train:
                spawned += 1

        logger.info(f"Spawned {spawned}/{count} trains after {attempts} attempts.")

    def tick(self):
        """Advance the simulation by one time step."""
        with self._lock:
            self.tick_count += 1
            active = 0
            blocked = 0
            arrived = 0

            for train in self.trains.values():
                if train.status == TrainStatus.ARRIVED:
                    arrived += 1
                    continue
                if train.status not in (TrainStatus.EN_ROUTE, TrainStatus.BLOCKED):
                    continue

                train.elapsed_ticks += 1

                # Check if train has reached destination
                if train.next_node is None:
                    train.status = TrainStatus.ARRIVED
                    arrived += 1
                    # Free last occupied edge
                    if train.route_index > 0:
                        prev_edge = f"{train.route[train.route_index - 1]}->{train.current_node}"
                        self.edge_occupancy.pop(prev_edge, None)
                    logger.info(f"[{train.train_id}] ARRIVED at {train.destination_name} "
                                f"({train.distance_traveled:.1f} km, {train.elapsed_ticks} ticks)")
                    continue

                # Calculate how far the train can move this tick
                distance_this_tick = train.speed_kmh * (self.tick_seconds / 3600.0)

                # Try to advance along the route
                moved = False
                remaining_distance = distance_this_tick

                while remaining_distance > 0 and train.next_node is not None:
                    u = train.current_node
                    v = train.next_node
                    edge_key = f"{u}->{v}"

                    # Check edge occupancy
                    occupant = self.edge_occupancy.get(edge_key)
                    if occupant and occupant != train.train_id:
                        # Edge is occupied by another train — BLOCKED
                        train.status = TrainStatus.BLOCKED
                        train.blocked_ticks += 1
                        blocked += 1
                        break

                    # Calculate edge distance
                    u_data = self.router.G.nodes[u]
                    v_data = self.router.G.nodes[v]
                    edge_dist = haversine(
                        float(u_data['x']), float(u_data['y']),
                        float(v_data['x']), float(v_data['y'])
                    )

                    if edge_dist <= remaining_distance:
                        # Train can fully traverse this edge
                        # Free previous edge
                        if train.route_index > 0:
                            prev_edge = f"{train.route[train.route_index - 1]}->{u}"
                            self.edge_occupancy.pop(prev_edge, None)

                        # Occupy new edge and advance
                        self.edge_occupancy[edge_key] = train.train_id
                        train.route_index += 1
                        train.distance_traveled += edge_dist
                        remaining_distance -= edge_dist
                        moved = True
                        train.status = TrainStatus.EN_ROUTE
                    else:
                        # Train partially traverses — occupy edge but don't advance node
                        self.edge_occupancy[edge_key] = train.train_id
                        train.distance_traveled += remaining_distance
                        remaining_distance = 0
                        moved = True
                        train.status = TrainStatus.EN_ROUTE

                if moved:
                    active += 1
                elif train.status != TrainStatus.BLOCKED:
                    active += 1

            # Record tick stats
            stats = {
                "tick": self.tick_count,
                "active": active,
                "blocked": blocked,
                "arrived": arrived,
                "total_trains": len(self.trains),
                "occupied_edges": len(self.edge_occupancy),
            }
            self.history.append(stats)
            return stats

    def get_train_positions(self) -> List[dict]:
        """Returns current position data for all trains (for the dashboard)."""
        positions = []
        with self._lock:
            for train in self.trains.values():
                node_data = self.router.G.nodes[train.current_node]
                positions.append({
                    "id": train.train_id,
                    "lat": float(node_data['y']),
                    "lon": float(node_data['x']),
                    "status": train.status.value,
                    "origin": train.origin_name,
                    "destination": train.destination_name,
                    "progress": round(train.progress_pct, 1),
                    "distance_km": round(train.distance_traveled, 1),
                    "total_km": round(train.total_route_distance, 1),
                    "speed_kmh": train.speed_kmh,
                    "blocked_ticks": train.blocked_ticks,
                })
        return positions

    def get_stats(self) -> dict:
        """Returns the latest tick stats."""
        if self.history:
            return self.history[-1]
        return {"tick": 0, "active": 0, "blocked": 0, "arrived": 0,
                "total_trains": 0, "occupied_edges": 0}

    def reset(self):
        """Reset the simulation."""
        with self._lock:
            self.trains.clear()
            self.edge_occupancy.clear()
            self.history.clear()
            self.tick_count = 0
            self._train_counter = 0
            self.running = False
        logger.info("Simulation reset.")


def add_route_from_nodes(router: RailwayRouter):
    """Monkey-patch a route_from_nodes method onto the router for direct node-to-node routing."""
    def route_from_nodes(self, start_node: str, end_node: str):
        try:
            from networkx import astar_path
            path = astar_path(
                self.G, source=start_node, target=end_node,
                heuristic=self._heuristic, weight=self._weight
            )
            return path
        except Exception:
            return None
    
    import types
    router.route_from_nodes = types.MethodType(route_from_nodes, router)


# ── CLI Test Harness ──────────────────────────────────────────────────────
def main():
    import argparse
    import geopandas as gpd

    parser = argparse.ArgumentParser(description="ARTEMIS Train Simulation")
    parser.add_argument("country", help="Country code (e.g., uk)")
    parser.add_argument("--trains", type=int, default=5, help="Number of trains")
    parser.add_argument("--ticks", type=int, default=300, help="Simulation ticks to run")
    parser.add_argument("--speed", type=float, default=120.0, help="Train speed in km/h")
    parser.add_argument("--tick-seconds", type=int, default=60, help="Simulated seconds per tick")
    args = parser.parse_args()

    print("=" * 60)
    print(f"ARTEMIS Train Simulation - {args.country.upper()}")
    print(f"Trains: {args.trains} | Ticks: {args.ticks} | Speed: {args.speed} km/h")
    print("=" * 60)

    # Load router
    router = RailwayRouter(args.country)
    add_route_from_nodes(router)

    # Load stations
    base_dir = Path(__file__).resolve().parent.parent
    stations_path = base_dir / "data" / "processed" / "geojson" / args.country / f"{args.country}_stations.geojson"

    if not stations_path.exists():
        print(f"Error: Stations file not found: {stations_path}")
        sys.exit(1)

    gdf = gpd.read_file(stations_path)
    stations = []
    
    # Find the name column — pyrosm may export it differently
    name_col = None
    for candidate in ['name', 'tags.name', 'Name', 'NAME']:
        if candidate in gdf.columns:
            name_col = candidate
            break
    
    if name_col:
        named = gdf[gdf[name_col].notna() & (gdf[name_col] != '')]
        for _, row in named.iterrows():
            if row.geometry and row.geometry.geom_type == 'Point':
                stations.append({"name": str(row[name_col]), "lat": row.geometry.y, "lon": row.geometry.x})
    else:
        # No name column — use all Point geometries with index-based names
        for idx, row in gdf.iterrows():
            if row.geometry and row.geometry.geom_type == 'Point':
                stations.append({"name": f"Station #{idx}", "lat": row.geometry.y, "lon": row.geometry.x})

    print(f"Loaded {len(stations)} stations for {args.country}.")

    # Create simulation
    sim = RailwaySimulation(router, tick_seconds=args.tick_seconds)
    sim.spawn_random_trains(stations, count=args.trains, speed_kmh=args.speed)

    # Run simulation
    print(f"\nRunning {args.ticks} ticks...\n")
    for i in range(args.ticks):
        stats = sim.tick()

        # Print progress every 50 ticks
        if (i + 1) % 50 == 0 or stats['arrived'] == stats['total_trains']:
            print(f"  Tick {stats['tick']:>4d}: "
                  f"Active={stats['active']}, Blocked={stats['blocked']}, "
                  f"Arrived={stats['arrived']}/{stats['total_trains']}, "
                  f"Edges occupied={stats['occupied_edges']}")

        if stats['arrived'] == stats['total_trains']:
            print(f"\n✓ All trains arrived after {stats['tick']} ticks!")
            break

    # Final summary
    print("\n" + "=" * 60)
    print("SIMULATION COMPLETE")
    print("=" * 60)
    for train in sim.trains.values():
        emoji = "✓" if train.status == TrainStatus.ARRIVED else "…"
        print(f"  {emoji} {train.train_id}: {train.origin_name} → {train.destination_name} "
              f"| {train.distance_traveled:.1f} km | {train.progress_pct:.0f}% "
              f"| Blocked {train.blocked_ticks}x | {train.status.value}")


if __name__ == "__main__":
    main()
