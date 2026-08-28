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
from stable_baselines3.common.vec_env import SubprocVecEnv
from stable_baselines3.common.callbacks import CheckpointCallback

from versions.v1.scripts.train_env import ArtemisTrainEnv

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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train PPO Brain for ARTEMIS")
    parser.add_argument("--country", type=str, default="uk", help="Country code to train on")
    parser.add_argument("--steps", type=int, default=100000, help="Total timesteps to train")
    parser.add_argument("--cores", type=int, default=16, help="Number of CPU cores to use for vectorized environments")
    parser.add_argument("--agents", type=int, default=4, help="Number of trains in the simulation")
    args = parser.parse_args()

    print(f"Setting up Vectorized Environment with {args.cores} cores for country: {args.country}")
    print(f"Number of Agents (Trains): {args.agents}")
    
    # Create the vectorized environment
    env = SubprocVecEnv([make_env(args.country, i, args.agents) for i in range(args.cores)])

    # Initialize the PPO model
    # We use MlpPolicy since our observation space is a 1D vector (speed, limit, dist)
    model = PPO("MlpPolicy", env, verbose=1, tensorboard_log="./versions/v1/logs/ppo_artemis_tensorboard/")

    # Save a checkpoint every 10,000 steps
    os.makedirs("versions/v1/models", exist_ok=True)
    checkpoint_callback = CheckpointCallback(
        save_freq=10000,
        save_path="./versions/v1/models/",
        name_prefix=f"ppo_artemis_{args.country}"
    )

    print("Starting training...")
    try:
        model.learn(total_timesteps=args.steps, callback=checkpoint_callback)
    except KeyboardInterrupt:
        print("Training interrupted manually. Saving current model...")
    finally:
        print("Saving final model...")
        model.save(f"./versions/v1/models/ppo_artemis_{args.country}_final")
        env.close()
        print("Done.")
