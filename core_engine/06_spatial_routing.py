#!/usr/bin/env python3
"""
ARTEMIS - Phase 3: Spatial Indexing & Routing
=============================================
This script provides the RailwayRouter class to:
1. Load GraphML networks.
2. Build a KD-Tree spatial index of all railway nodes.
3. Snap GPS coordinates to the nearest valid railway node.
4. Calculate the absolute shortest path using A* and the Haversine formula.
"""

import os
import sys
import math
import argparse
import logging
from pathlib import Path

import networkx as nx
from scipy.spatial import cKDTree

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def haversine(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance in kilometers between two points 
    on the earth (specified in decimal degrees).
    """
    # convert decimal degrees to radians 
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])

    # haversine formula 
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a)) 
    r = 6371 # Radius of earth in kilometers
    return c * r

class RailwayRouter:
    def __init__(self, country_code: str, graph_dir: str = None):
        if graph_dir is None:
            # Default to ARTEMIS/data/processed/graph
            graph_dir = Path(__file__).resolve().parent.parent / "data" / "processed" / "graph"
            
        # Support multiple countries like "france,germany"
        if isinstance(country_code, str):
            self.countries = [c.strip() for c in country_code.split(",")]
        else:
            self.countries = country_code
            
        self.country_code = ",".join(self.countries)
        self.graph_dir = Path(graph_dir)
        
        self.G = None
        self.kdtree = None
        self.node_list = []
        
        self._load_graph()
        self._build_spatial_index()
        
    def _load_graph(self):
        combined_raw_G = nx.DiGraph()
        
        for country in self.countries:
            graph_path = self.graph_dir / country / f"{country}_network.graphml"
            if not graph_path.exists():
                raise FileNotFoundError(f"GraphML not found: {graph_path}. Please run Phase 2 first.")
                
            logger.info(f"Loading graph for {country} from {graph_path}...")
            raw_G = nx.read_graphml(graph_path)
            logger.info(f"  {country} graph loaded: {raw_G.number_of_nodes():,} nodes, {raw_G.number_of_edges():,} edges")
            
            # Compose with the combined graph
            combined_raw_G = nx.compose(combined_raw_G, raw_G)
            
        logger.info(f"Combined raw graph: {combined_raw_G.number_of_nodes():,} nodes, {combined_raw_G.number_of_edges():,} edges")
        
        # Extract the largest strongly connected component (the main national/international network)
        # This prevents the KD-Tree from snapping to disconnected local lines or abandoned spurs.
        largest_cc = max(nx.strongly_connected_components(combined_raw_G), key=len)
        self.G = combined_raw_G.subgraph(largest_cc).copy()
        logger.info(f"Extracted main network component: {self.G.number_of_nodes():,} nodes, {self.G.number_of_edges():,} edges")
        
    def _build_spatial_index(self):
        """Builds a KD-Tree from node coordinates for O(log n) nearest neighbor lookups."""
        logger.info("Building KD-Tree spatial index...")
        
        coords = []
        self.node_list = list(self.G.nodes(data=True))
        
        for node_id, data in self.node_list:
            # We expect nodes to have 'x' (lon) and 'y' (lat) attributes from Phase 2
            x = float(data.get('x', 0.0))
            y = float(data.get('y', 0.0))
            coords.append((y, x)) # scipy cKDTree likes (lat, lon) or (y, x)
            
        self.kdtree = cKDTree(coords)
        logger.info(f"KD-Tree built with {len(coords):,} points.")
        
    def find_nearest_node(self, lat: float, lon: float):
        """Finds the nearest railway node to a given GPS coordinate."""
        distance, index = self.kdtree.query((lat, lon))
        node_id, data = self.node_list[index]
        return node_id, data, distance

    def _heuristic(self, u, v):
        """Heuristic for A*: straight-line Haversine distance between u and v."""
        u_data = self.G.nodes[u]
        v_data = self.G.nodes[v]
        return haversine(
            float(u_data['x']), float(u_data['y']),
            float(v_data['x']), float(v_data['y'])
        )

    def _weight(self, u, v, d):
        """Weight function for edges: actual Haversine distance between nodes."""
        return self._heuristic(u, v)

    def route(self, start_lat: float, start_lon: float, end_lat: float, end_lon: float):
        """
        Calculates the shortest railway path between two GPS coordinates.
        Returns the path (list of node IDs) and the total distance in km.
        """
        # 1. Snap to nearest nodes
        start_node, start_data, _ = self.find_nearest_node(start_lat, start_lon)
        end_node, end_data, _ = self.find_nearest_node(end_lat, end_lon)
        
        logger.info(f"Routing from {start_node} to {end_node}...")
        
        # 2. Run A* Algorithm
        try:
            path = nx.astar_path(
                self.G, 
                source=start_node, 
                target=end_node, 
                heuristic=self._heuristic, 
                weight=self._weight
            )
            
            # Calculate total distance
            total_distance = sum(self._weight(path[i], path[i+1], None) for i in range(len(path)-1))
            
            return path, total_distance
            
        except nx.NetworkXNoPath:
            logger.error("No path found between these nodes. They might be disconnected segments.")
            return None, 0.0

def main():
    parser = argparse.ArgumentParser(description="ARTEMIS - Spatial Routing Engine")
    parser.add_argument("country", help="Country code (e.g., uk, france, us)")
    parser.add_argument("--start", type=str, required=True, help="Start lat,lon (e.g., 51.5,-0.12)")
    parser.add_argument("--end", type=str, required=True, help="End lat,lon (e.g., 55.95,-3.18)")
    
    args = parser.parse_args()
    
    try:
        start_lat, start_lon = map(float, args.start.split(","))
        end_lat, end_lon = map(float, args.end.split(","))
    except ValueError:
        logger.error("Invalid coordinates. Please use format: lat,lon")
        sys.exit(1)
        
    print("="*60)
    print(f"ARTEMIS Routing Engine - {args.country.upper()}")
    print("="*60)
    
    router = RailwayRouter(args.country)
    
    path, distance = router.route(start_lat, start_lon, end_lat, end_lon)
    
    if path:
        print("\n" + "="*60)
        print(f"✓ Route found!")
        print(f"Nodes traversed: {len(path):,}")
        print(f"Total distance: {distance:.2f} km")
        print("="*60)

if __name__ == "__main__":
    main()
