import os
import sys
import time
import threading
import logging
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from stable_baselines3 import PPO
from versions.v2.scripts.train_env_v2 import ArtemisTrainEnv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RLSimController:
    def __init__(self, country="uk", trains=None, tick_seconds=1.0):
        self.country = country
        self.train_configs = trains
        self.num_agents = len(trains) if trains else 4
        self.tick_seconds = tick_seconds
        
        self.env = None
        self.model = None
        
        self.running = False
        self.thread = None
        
        self.tick_count = 0
        self.obs = None
        self.last_rewards = None
        self.all_arrived = False
        
        self._lock = threading.Lock()
        self._pending_trains = []
        
    def start(self):
        if self.running:
            return
            
        logger.info(f"Loading PPO Model for RL Simulation ({self.country})...")
        model_path = f"versions/v2/models/ppo_artemis_{self.country}_final.zip"
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found at {model_path}")
            
        self.model = PPO.load(model_path)
        
        logger.info("Initializing ArtemisTrainEnv...")
        self.env = ArtemisTrainEnv(country_code=self.country, num_agents=self.num_agents, train_configs=self.train_configs)
        self.obs, _ = self.env.reset()
        self.last_rewards = [0.0] * self.num_agents
        self.tick_count = 0
        self.all_arrived = False
        
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)

    def add_trains(self, configs):
        """Queue new trains to be added to the running simulation."""
        with self._lock:
            self._pending_trains.extend(configs)
            
    def _inject_pending_trains(self):
        """Inject any pending trains into the environment. Called from the run loop."""
        with self._lock:
            pending = list(self._pending_trains)
            self._pending_trains.clear()
        
        if not pending:
            return
            
        for config in pending:
            success = self.env.add_agent(config)
            if success:
                logger.info(f"Hot-added train #{self.env.num_agents - 1}")
        
        # Update controller state to match new agent count
        self.num_agents = self.env.num_agents
        self.last_rewards = list(self.last_rewards) + [0.0] * len(pending)
        # Rebuild obs to match new shape
        self.obs = self.env._get_obs()
        # Reset arrived flag since new trains need to finish
        self.all_arrived = False
            
    def _run_loop(self):
        while self.running:
            # Inject any hot-added trains
            self._inject_pending_trains()
            
            num = self.env.num_agents
            actions = []
            for i in range(num):
                obs_i = self.obs[i] if i < len(self.obs) else self.obs[-1]
                action, _states = self.model.predict(obs_i, deterministic=True)
                actions.append(action)
                
            self.obs, rewards, terminated, truncated, info = self.env.step(actions)
            self.last_rewards = rewards
            
            with self._lock:
                self.tick_count += 1
                
            if terminated:
                logger.info("All trains have reached their destinations!")
                self.all_arrived = True
                # Don't exit — wait for more trains to be added
                while self.running and self.all_arrived and not self._pending_trains:
                    time.sleep(0.5)
                continue
            
            if truncated:
                logger.warning("Simulation truncated (max steps reached).")
                self.all_arrived = True
                while self.running and self.all_arrived and not self._pending_trains:
                    time.sleep(0.5)
                continue
                
            time.sleep(self.tick_seconds)
            
    def get_state(self):
        if not self.running or not self.env:
            return {"running": False, "trains": [], "stats": {}}
            
        trains = []
        with self._lock:
            num = self.env.num_agents
            for i in range(num):
                status = "ARRIVED" if self.env.reached_destination[i] else "EN_ROUTE"
                node = self.env.current_nodes[i]
                node_data = self.env.router.G.nodes[node]
                
                # Calculate progress and distances
                dist_to_go = self.env.router._heuristic(self.env.current_nodes[i], self.env.target_nodes[i])
                
                # Approximate progress
                progress = 100.0 if status == "ARRIVED" else (self.env.path_indices[i] / max(1, len(self.env.optimal_paths[i]))) * 100.0
                
                trains.append({
                    "id": f"RL_T{i}",
                    "lat": float(node_data['y']),
                    "lon": float(node_data['x']),
                    "status": status,
                    "origin": str(self.env.optimal_paths[i][0]) if len(self.env.optimal_paths[i]) > 0 else str(node),
                    "destination": str(self.env.target_nodes[i]),
                    "progress": round(progress, 1),
                    "distance_km": round(dist_to_go, 1),
                    "total_km": round(dist_to_go, 1), 
                    "speed_kmh": float(self.env.train_speeds[i]),
                    "reward": float(self.last_rewards[i]) if i < len(self.last_rewards) else 0.0
                })
                
            stats = {
                "tick": self.tick_count,
                "active": sum(1 for t in trains if t['status'] == 'EN_ROUTE'),
                "blocked": 0,
                "arrived": sum(1 for t in trains if t['status'] == 'ARRIVED'),
                "total_trains": num
            }
            
        return {
            "running": True,
            "trains": trains,
            "stats": stats
        }

    def get_active_edges(self):
        """Returns a set of edge tuples (u, v) for edges currently occupied by trains."""
        if not self.running or not self.env:
            return []
        
        edges = []
        with self._lock:
            for i in range(self.env.num_agents):
                if self.env.reached_destination[i]:
                    continue
                idx = int(self.env.path_indices[i])
                path = self.env.optimal_paths[i]
                if idx < len(path) - 1:
                    edges.append((path[idx], path[idx + 1]))
        return edges
