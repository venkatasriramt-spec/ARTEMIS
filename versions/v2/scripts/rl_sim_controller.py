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
        
        self._lock = threading.Lock()
        
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
        
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
            
    def _run_loop(self):
        while self.running:
            actions = []
            for i in range(self.num_agents):
                action, _states = self.model.predict(self.obs[i], deterministic=True)
                actions.append(action)
                
            self.obs, rewards, terminated, truncated, info = self.env.step(actions)
            self.last_rewards = rewards
            
            with self._lock:
                self.tick_count += 1
                
            if terminated:
                logger.info("All trains have reached their destinations! Terminating script.")
                os._exit(0)
            
            if truncated:
                logger.warning("Simulation truncated (max steps reached)! Terminating script.")
                os._exit(0)
                
            time.sleep(self.tick_seconds)
            
    def get_state(self):
        if not self.running or not self.env:
            return {"running": False, "trains": [], "stats": {}}
            
        trains = []
        with self._lock:
            for i in range(self.num_agents):
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
                    "reward": float(self.last_rewards[i])
                })
                
            stats = {
                "tick": self.tick_count,
                "active": sum(1 for t in trains if t['status'] == 'EN_ROUTE'),
                "blocked": 0,
                "arrived": sum(1 for t in trains if t['status'] == 'ARRIVED'),
                "total_trains": self.num_agents
            }
            
        return {
            "running": True,
            "trains": trains,
            "stats": stats
        }
