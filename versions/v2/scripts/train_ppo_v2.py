import os
import sys
from pathlib import Path

# Prevent sub-processes from spawning too many threads and crashing the VM
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

import argparse
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import SubprocVecEnv, VecEnv
from stable_baselines3.common.callbacks import CheckpointCallback
from gymnasium import spaces
import numpy as np

from versions.v2.scripts.train_env_v2 import ArtemisTrainEnv

def make_env(country_code, rank, num_agents, seed=0):
    """
    Utility function for multiprocessed env.
    
    :param country_code: (str) the country code to load
    :param rank: (int) index of the subprocess
    :param num_agents: (int) number of trains in the simulation
    :param seed: (int) the initial seed for RNG
    """
    def _init():
        env = ArtemisTrainEnv(country_code=country_code, num_agents=num_agents)
        env.reset(seed=seed + rank)
        return env
    return _init

class FlattenMultiAgentVecEnv(VecEnv):
    """
    Tricks Stable-Baselines3 into training a Decentralized Shared-Policy.
    Takes a SubprocVecEnv where each environment returns (agents, obs_dim).
    Flattens it so SB3 thinks it's interacting with (cores * agents) separate single-agent environments!
    """
    def __init__(self, venv, num_agents):
        self.venv = venv
        self.num_agents = num_agents
        
        # SB3 thinks the environment only accepts 1 action (Discrete 3) and outputs 1 observation (Box 3)
        single_obs_space = spaces.Box(low=0.0, high=np.array([300.0, 300.0, 10000.0], dtype=np.float32), dtype=np.float32)
        single_act_space = spaces.Discrete(3)
        
        super().__init__(
            num_envs=venv.num_envs * num_agents,
            observation_space=single_obs_space,
            action_space=single_act_space
        )

    def step_async(self, actions):
        # SB3 passes a 1D array of length (cores * agents)
        # We reshape it into (cores, agents) and pass it to SubprocVecEnv
        reshaped_actions = actions.reshape(self.venv.num_envs, self.num_agents)
        self.venv.step_async(reshaped_actions)

    def step_wait(self):
        obs, rewards, dones, infos = self.venv.step_wait()
        
        # obs is (cores, agents, 3) -> (cores * agents, 3)
        obs = obs.reshape(self.num_envs, -1)
        
        # rewards is (cores, agents) -> (cores * agents)
        rewards = rewards.reshape(self.num_envs)
        
        # dones is (cores) -> duplicate for all agents (cores * agents)
        dones = np.repeat(dones, self.num_agents)
        
        # infos is a tuple of dicts -> duplicate dicts
        new_infos = []
        for info in infos:
            new_infos.extend([info] * self.num_agents)
            
        return obs, rewards, dones, new_infos

    def reset(self):
        obs = self.venv.reset()
        return obs.reshape(self.num_envs, -1)

    def close(self):
        self.venv.close()
        
    def env_is_wrapped(self, wrapper_class, indices=None):
        return [False] * self.num_envs
        
    def env_method(self, method_name, *method_args, indices=None, **method_kwargs):
        return self.venv.env_method(method_name, *method_args, indices=None, **method_kwargs)

    def get_attr(self, attr_name, indices=None):
        return self.venv.get_attr(attr_name, indices=None)

    def set_attr(self, attr_name, value, indices=None):
        return self.venv.set_attr(attr_name, value, indices=None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train V2 Decentralized PPO Brain for ARTEMIS")
    parser.add_argument("--country", type=str, default="uk", help="Country code to train on")
    parser.add_argument("--steps", type=int, default=100000, help="Total timesteps to train")
    parser.add_argument("--cores", type=int, default=10, help="Number of CPU cores to use for vectorized environments")
    parser.add_argument("--agents", type=int, default=4, help="Number of trains in the simulation")
    args = parser.parse_args()

    print(f"Setting up Vectorized Environment with {args.cores} cores for country: {args.country}")
    print(f"Number of Agents (Trains): {args.agents}")
    
    # Create the SubprocVecEnv (Outputs shape: cores x agents x obs_dim)
    base_env = SubprocVecEnv([make_env(args.country, i, args.agents) for i in range(args.cores)])
    
    # Wrap it to flatten the agents! (Outputs shape: (cores * agents) x obs_dim)
    env = FlattenMultiAgentVecEnv(base_env, num_agents=args.agents)

    # Initialize the PPO model
    # We use MlpPolicy since our observation space is a 1D vector (speed, limit, dist)
    model = PPO("MlpPolicy", env, verbose=1, tensorboard_log="./versions/v2/logs/ppo_artemis_tensorboard/")

    # Save a checkpoint every 10,000 steps
    os.makedirs("versions/v2/models", exist_ok=True)
    checkpoint_callback = CheckpointCallback(
        save_freq=10000,
        save_path="./versions/v2/models/",
        name_prefix=f"ppo_artemis_{args.country}"
    )

    print("Starting training...")
    try:
        model.learn(total_timesteps=args.steps, callback=checkpoint_callback)
    except KeyboardInterrupt:
        print("Training interrupted manually. Saving current model...")
    finally:
        print("Saving final model...")
        model.save(f"./versions/v2/models/ppo_artemis_{args.country}_final")
        env.close()
        print("Done.")
