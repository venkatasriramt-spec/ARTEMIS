"""
ARTEMIS - Model Re-exporter
Extracts PPO model policy weights into clean .pth files that AV software won't flag.
The model can be loaded back in two ways:
    1. Original SB3 zip (for running the visualization server)  
    2. Clean .pth files (for sharing / committing to GitHub)
"""
import os
import sys
import zipfile
import json
import shutil
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

def reexport_model(zip_path: str, out_dir: str):
    """
    Extracts the neural network policy weights from an SB3 zip into
    a clean directory containing only .pth files (no pickle data blob).
    """
    zip_path = Path(zip_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Re-exporting: {zip_path}")

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
        print(f"  Contents: {names}")

        # Extract only the pure PyTorch weight files (.pth) - these are safe
        for name in names:
            if name.endswith('.pth'):
                out_path = out_dir / name
                with zf.open(name) as src, open(out_path, 'wb') as dst:
                    dst.write(src.read())
                print(f"  Extracted: {name} -> {out_path}")

        # Also save the system info and version (plain text, totally safe)
        for name in ['_stable_baselines3_version', 'system_info.txt']:
            if name in names:
                out_path = out_dir / name
                with zf.open(name) as src, open(out_path, 'wb') as dst:
                    dst.write(src.read())
                print(f"  Extracted: {name} -> {out_path}")

    print(f"  Done! Clean weights saved to: {out_dir}/")
    print()


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent.parent.parent  # ARTEMIS root

    models = [
        ("versions/v2/models/ppo_artemis_uk_final.zip", "versions/v2/models/ppo_artemis_uk_weights"),
    ]

    # Also re-export v1 if it exists
    v1_path = base / "versions/v1/models/ppo_artemis_uk_final.zip"
    if v1_path.exists():
        models.append(
            ("versions/v1/models/ppo_artemis_uk_final.zip", "versions/v1/models/ppo_artemis_uk_weights")
        )

    for zip_rel, out_rel in models:
        zip_abs = base / zip_rel
        out_abs = base / out_rel
        if zip_abs.exists():
            reexport_model(str(zip_abs), str(out_abs))
        else:
            print(f"  Skipped (not found): {zip_abs}")

    print("=" * 60)
    print("Re-export complete.")
    print()
    print("IMPORTANT: The original .zip files are still required by the")
    print("visualization server to run the RL simulation. To avoid AV")
    print("flags when committing to GitHub:")
    print()
    print("  1. Add the .zip files to .gitignore (recommended)")
    print("  2. Commit the clean _weights/ directories instead")
    print("  3. Re-generate the .zip files locally from the weights when needed")
