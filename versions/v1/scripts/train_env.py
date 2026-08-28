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
    Multi-Agent version using Centralized PPO.
    """
    metadata = {"render_modes": ["human"]}

    def __init__(self, country_code="uk", num_agents=4):
        super(ArtemisTrainEnv, self).__init__()
        
        self.num_agents = num_agents
        
        # 1. Load your Phase 3 Graph and Router
        self.router = RailwayRouter(country_code)
        self.graph = self.router.G
        
        # 2. Define the Action Space (MultiDiscrete)
        # 0: Brake (-20 km/h)
        # 1: Maintain Speed
        # 2: Accelerate (+20 km/h)
        # One action per train
        self.action_space = spaces.MultiDiscrete([3] * self.num_agents)
        
        # 3. Define the Observation Space
        # Flattened Array of size (num_agents * 3)
        # Each train has: [current_train_speed, current_edge_max_speed, distance_to_destination]
        lows = np.array([0.0, 0.0, 0.0] * self.num_agents, dtype=np.float32)
        highs = np.array([300.0, 300.0, 10000.0] * self.num_agents, dtype=np.float32)
        self.observation_space = spaces.Box(low=lows, high=highs, dtype=np.float32)
        
        self.current_nodes = [None] * self.num_agents
        self.target_nodes = [None] * self.num_agents
        self.train_speeds = np.zeros(self.num_agents, dtype=np.float32)
        self.optimal_paths = [[] for _ in range(self.num_agents)]
        self.path_indices = np.zeros(self.num_agents, dtype=int)
        
        self.steps = 0
        self.max_steps = 1000 # Prevent infinite loops
        self.reached_destination = np.zeros(self.num_agents, dtype=bool)

    def reset(self, seed=None, options=None):
        """Resets the environment for a new training episode."""
        super().reset(seed=seed)
        
        nodes = list(self.graph.nodes())
        
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
        reward = 0.0
        terminated = False
        truncated = False
        
        for i in range(self.num_agents):
            if self.reached_destination[i]:
                continue
                
            action = actions[i]
            
            # Process Action
            if action == 0:
                self.train_speeds[i] = max(0.0, self.train_speeds[i] - 20.0)
            elif action == 1:
                pass
            elif action == 2:
                self.train_speeds[i] = min(300.0, self.train_speeds[i] + 20.0)
                
            # Get current edge properties
            edge_max_speed = 100.0
            idx = self.path_indices[i]
            if idx < len(self.optimal_paths[i]) - 1:
                curr_node = self.optimal_paths[i][idx]
                next_node = self.optimal_paths[i][idx + 1]
                edge_data = self.graph.get_edge_data(curr_node, next_node)
                
                if edge_data:
                    raw_max = edge_data.get('maxspeed', '100')
                    if isinstance(raw_max, str) and raw_max.isdigit():
                        edge_max_speed = float(raw_max)
                    elif not isinstance(raw_max, str):
                        try:
                            edge_max_speed = float(raw_max)
                        except:
                            pass
                            
                # Penalty for speeding
                if self.train_speeds[i] > edge_max_speed:
                    reward -= 5.0
                    
                # Move train to next node if moving
                if self.train_speeds[i] > 0:
                    self.current_nodes[i] = next_node
                    self.path_indices[i] += 1
                    reward += 1.0 # Reward for progress
                    
            # Check destination
            if self.current_nodes[i] == self.target_nodes[i]:
                self.reached_destination[i] = True
                self.train_speeds[i] = 0.0
                reward += 100.0
        
        # Collision Detection
        active_nodes = [self.current_nodes[i] for i in range(self.num_agents) if not self.reached_destination[i]]
        if len(active_nodes) != len(set(active_nodes)):
            reward -= 1000.0
            
        # Time penalty for all active agents
        num_active = np.sum(~self.reached_destination)
        reward -= 1.0 * num_active
        
        if np.all(self.reached_destination):
            terminated = True
            
        if self.steps >= self.max_steps:
            truncated = True

        return self._get_obs(), float(reward), terminated, truncated, {}

    def _get_obs(self):
        """Constructs the flattened state array for all agents."""
        obs = []
        for i in range(self.num_agents):
            distance_left = self.router._heuristic(self.current_nodes[i], self.target_nodes[i])
            
            edge_max_speed = 100.0 
            idx = self.path_indices[i]
            if idx < len(self.optimal_paths[i]) - 1:
                curr_node = self.optimal_paths[i][idx]
                next_node = self.optimal_paths[i][idx + 1]
                edge_data = self.graph.get_edge_data(curr_node, next_node)
                if edge_data:
                    raw_max = edge_data.get('maxspeed', '100')
                    if isinstance(raw_max, str) and raw_max.isdigit():
                        edge_max_speed = float(raw_max)
                    elif not isinstance(raw_max, str):
                        try:
                            edge_max_speed = float(raw_max)
                        except:
                            pass
                            
            obs.extend([self.train_speeds[i], edge_max_speed, distance_left])
            
        return np.array(obs, dtype=np.float32)

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
