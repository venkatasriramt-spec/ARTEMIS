import gymnasium as gym
from gymnasium import spaces
import numpy as np
import networkx as nx
import logging

# Set up logging for the env
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

import importlib
import sys
from pathlib import Path
# We need to point sys.path to the root directory to access core_engine
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))
spatial_routing = importlib.import_module("core_engine.06_spatial_routing")
RailwayRouter = spatial_routing.RailwayRouter

class ArtemisTrainEnv(gym.Env):
    """
    Custom Gymnasium Environment for ARTEMIS Train Routing.
    Multi-Agent version using Decentralized Shared-Radar RL.
    """
    metadata = {"render_modes": ["human"]}

    def __init__(self, country_code="uk", num_agents=4, train_configs=None):
        super(ArtemisTrainEnv, self).__init__()
        
        self.num_agents = num_agents
        self.train_configs = train_configs
        
        # 1. Load your Phase 3 Graph and Router
        self.router = RailwayRouter(country_code)
        self.graph = self.router.G
        
        # 2. Define the Action Space (MultiDiscrete)
        # 0: Brake (-20 km/h)
        # 1: Maintain Speed
        # 2: Accelerate (+20 km/h)
        # One action per train
        self.action_space = spaces.MultiDiscrete([3] * self.num_agents)
        
        # 3. Define the Observation Space (Local Radar for each agent)
        # We define the space for all agents here, but SB3 will see it flattened later.
        # Shape: (num_agents, 3)
        # Radar features: [current_speed, edge_speed_limit, dist_to_nearest_train]
        lows = np.array([[0.0, 0.0, 0.0] for _ in range(self.num_agents)], dtype=np.float32)
        highs = np.array([[300.0, 300.0, 10000.0] for _ in range(self.num_agents)], dtype=np.float32)
        self.observation_space = spaces.Box(low=lows, high=highs, dtype=np.float32)
        
        self.current_nodes = [None] * self.num_agents
        self.target_nodes = [None] * self.num_agents
        self.train_speeds = np.zeros(self.num_agents, dtype=np.float32)
        self.optimal_paths = [[] for _ in range(self.num_agents)]
        self.path_indices = np.zeros(self.num_agents, dtype=int)
        
        self.steps = 0
        self.max_steps = 20000 # Prevent infinite loops
        self.reached_destination = np.zeros(self.num_agents, dtype=bool)

    def reset(self, seed=None, options=None):
        """Resets the environment for a new training episode."""
        super().reset(seed=seed)
        
        nodes = list(self.graph.nodes())
        
        if self.train_configs and len(self.train_configs) == self.num_agents:
            for i, config in enumerate(self.train_configs):
                start_node, _, _ = self.router.find_nearest_node(config["start_lat"], config["start_lon"])
                end_node, _, _ = self.router.find_nearest_node(config["end_lat"], config["end_lon"])
                try:
                    path = nx.astar_path(
                        self.graph, start_node, end_node, 
                        heuristic=self.router._heuristic, weight=self.router._weight
                    )
                    self.current_nodes[i] = start_node
                    self.target_nodes[i] = end_node
                    self.optimal_paths[i] = path
                except nx.NetworkXNoPath:
                    logger.error(f"No path for train {i} between {start_node} and {end_node}. Defaulting to start node.")
                    self.current_nodes[i] = start_node
                    self.target_nodes[i] = start_node
                    self.optimal_paths[i] = [start_node]
        else:
            for i in range(self.num_agents):
                while True:
                    start = np.random.choice(nodes)
                    end = np.random.choice(nodes)
                    
                    if start == end:
                        continue
                        
                    # Ensure they are connected using A* router
                    try:
                        path = nx.astar_path(
                            self.graph, start, end, 
                            heuristic=self.router._heuristic, weight=self.router._weight
                        )
                        if len(path) > 1:
                            self.current_nodes[i] = start
                            self.target_nodes[i] = end
                            self.optimal_paths[i] = path
                            break
                    except nx.NetworkXNoPath:
                        continue

        self.path_indices = np.zeros(self.num_agents, dtype=int)
        self.train_speeds = np.zeros(self.num_agents, dtype=np.float32)
        self.reached_destination = np.zeros(self.num_agents, dtype=bool)
        self.steps = 0
        
        return self._get_obs(), {}

    def step(self, actions):
        """Steps the simulation forward by one time unit."""
        self.steps += 1
        
        # Helper to calculate Euclidean distance (Haversine approx for small distances)
        def calc_dist(n1, n2):
            lat1, lon1 = map(float, n1.split(','))
            lat2, lon2 = map(float, n2.split(','))
            return np.sqrt((lat1-lat2)**2 + (lon1-lon2)**2) * 111.0 # approx km
            
        def parse_speed(edge_dict):
            raw = edge_dict.get('maxspeed', 100.0)
            try:
                if isinstance(raw, str) and not raw.strip():
                    return 100.0
                return float(raw)
            except ValueError:
                return 100.0

        for i in range(self.num_agents):
            if self.reached_destination[i]:
                continue
                
            action = actions[i]
            if action == 0:
                self.train_speeds[i] = max(0.0, self.train_speeds[i] - 20.0)
            elif action == 2:
                self.train_speeds[i] = min(300.0, self.train_speeds[i] + 20.0)
                
            if self.train_speeds[i] > 0 and self.path_indices[i] < len(self.optimal_paths[i]) - 1:
                self.path_indices[i] += 1
                self.current_nodes[i] = self.optimal_paths[i][self.path_indices[i]]
                
            if self.current_nodes[i] == self.target_nodes[i]:
                self.reached_destination[i] = True
                self.train_speeds[i] = 0.0

        # Vectorized Rewards
        rewards = np.zeros(self.num_agents, dtype=np.float32)
        
        # Collision Detection (pairwise penalty)
        for i in range(self.num_agents):
            if self.reached_destination[i]:
                continue
            for j in range(i+1, self.num_agents):
                if not self.reached_destination[j] and self.current_nodes[i] == self.current_nodes[j]:
                    rewards[i] -= 1000.0
                    rewards[j] -= 1000.0
            
            # Progress reward and time penalty
            rewards[i] += 1.0 if self.train_speeds[i] > 0 else 0.0
            if self.reached_destination[i]:
                rewards[i] += 100.0
            rewards[i] -= 1.0

        # Build batched observation
        obs = np.zeros((self.num_agents, 3), dtype=np.float32)
        for i in range(self.num_agents):
            if self.reached_destination[i]:
                obs[i] = [0.0, 0.0, 10000.0]
                continue
                
            curr_node = self.current_nodes[i]
            idx = self.path_indices[i]
            next_node = self.optimal_paths[i][min(idx + 1, len(self.optimal_paths[i]) - 1)]
            
            limit = 100.0
            if self.graph.has_edge(curr_node, next_node):
                limit = parse_speed(self.graph[curr_node][next_node])
                
            if self.train_speeds[i] > limit:
                rewards[i] -= 5.0
                
            min_dist = 10000.0
            for j in range(self.num_agents):
                if i != j and not self.reached_destination[j]:
                    dist = calc_dist(curr_node, self.current_nodes[j])
                    if dist < min_dist:
                        min_dist = dist
                        
            obs[i] = [self.train_speeds[i], limit, min_dist]

        terminated = bool(np.all(self.reached_destination))
        truncated = self.steps >= self.max_steps

        return obs, rewards, terminated, truncated, {}

    def _get_obs(self):
        """Constructs the batched observation array for all agents."""
        obs = np.zeros((self.num_agents, 3), dtype=np.float32)
        
        def calc_dist(n1, n2):
            lat1, lon1 = map(float, n1.split(','))
            lat2, lon2 = map(float, n2.split(','))
            return np.sqrt((lat1-lat2)**2 + (lon1-lon2)**2) * 111.0 # approx km
            
        def parse_speed(edge_dict):
            raw = edge_dict.get('maxspeed', 100.0)
            try:
                if isinstance(raw, str) and not raw.strip():
                    return 100.0
                return float(raw)
            except ValueError:
                return 100.0

        for i in range(self.num_agents):
            if self.reached_destination[i]:
                obs[i] = [0.0, 0.0, 10000.0]
                continue
                
            curr_node = self.current_nodes[i]
            idx = self.path_indices[i]
            if len(self.optimal_paths[i]) > 0:
                next_node = self.optimal_paths[i][min(idx + 1, len(self.optimal_paths[i]) - 1)]
            else:
                next_node = curr_node
            
            limit = 100.0
            if self.graph.has_edge(curr_node, next_node):
                limit = parse_speed(self.graph[curr_node][next_node])
                
            min_dist = 10000.0
            for j in range(self.num_agents):
                if i != j and not self.reached_destination[j]:
                    dist = calc_dist(curr_node, self.current_nodes[j])
                    if dist < min_dist:
                        min_dist = dist
                        
            obs[i] = [self.train_speeds[i], limit, min_dist]
            
        return obs

    def add_agent(self, config):
        """Dynamically add a new train agent to the running environment."""
        start_node, _, _ = self.router.find_nearest_node(config["start_lat"], config["start_lon"])
        end_node, _, _ = self.router.find_nearest_node(config["end_lat"], config["end_lon"])
        
        try:
            path = nx.astar_path(
                self.graph, start_node, end_node,
                heuristic=self.router._heuristic, weight=self.router._weight
            )
        except nx.NetworkXNoPath:
            logger.error(f"No path between {start_node} and {end_node}. Skipping.")
            return False
        
        self.num_agents += 1
        self.current_nodes.append(start_node)
        self.target_nodes.append(end_node)
        self.optimal_paths.append(path)
        self.path_indices = np.append(self.path_indices, 0)
        self.train_speeds = np.append(self.train_speeds, np.float32(0.0))
        self.reached_destination = np.append(self.reached_destination, False)
        
        return True

    def render(self):
        """Optional: print current status to console."""
        print(f"--- Step {self.steps} ---")
        for i in range(self.num_agents):
            status = "ARRIVED" if self.reached_destination[i] else "EN ROUTE"
            print(f"Train {i} | {status} | Node: {self.current_nodes[i]} | Speed: {self.train_speeds[i]:.1f} | Dist: {self.router._heuristic(self.current_nodes[i], self.target_nodes[i]):.2f} km")

if __name__ == "__main__":
    # Test the environment
    env = ArtemisTrainEnv(country_code="uk", num_agents=4)
    obs, _ = env.reset()
    print("Initial observation shape:", obs.shape)
    for _ in range(5):
        obs, rew, term, trunc, info = env.step(env.action_space.sample())
        env.render()
        print(f"Reward: {rew}")
        if term or trunc:
            print("Terminated early (Collision or Finished).")
            break
