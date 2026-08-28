#!/usr/bin/env python3
"""
ARTEMIS - Full Pipeline Orchestrator
=====================================
Orchestrates the complete data extraction pipeline:
  1. Download PBF files from Geofabrik
  2. Extract railway data using pyrosm
  3. Convert to KML format
  4. Upload to Google Cloud Storage

Supports:
- Running individual steps or the full pipeline
- Country selection
- Overpass API fallback mode
- Detailed logging and progress reporting
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()

# ----- Configuration -----
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "countries.json"

# Data directories
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
GEOJSON_DIR = DATA_DIR / "processed" / "geojson"
KML_DIR = DATA_DIR / "processed" / "kml"
CACHE_DIR = DATA_DIR / "overpass_cache"
REPORTS_DIR = DATA_DIR / "reports"


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """Load country configuration from JSON."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_banner():
    """Print the ARTEMIS banner."""
    banner = """
    █████╗ ██████╗ ████████╗███████╗███╗   ███╗██╗███████╗
   ██╔══██╗██╔══██╗╚══██╔══╝██╔════╝████╗ ████║██║██╔════╝
   ███████║██████╔╝   ██║   █████╗  ██╔████╔██║██║███████╗
   ██╔══██║██╔══██╗   ██║   ██╔══╝  ██║╚██╔╝██║██║╚════██║
   ██║  ██║██║  ██║   ██║   ███████╗██║ ╚═╝ ██║██║███████║
   ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝     ╚═╝╚═╝╚══════╝
   Autonomous Railway Throughput & Management Intelligent System
    """
    console.print(Panel(banner, style="bold cyan", border_style="cyan"))


def step_download(config: dict, countries: list[str] | None = None, **kwargs) -> list[dict]:
    """Step 1: Download PBF files from Geofabrik."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("download_pbf", SCRIPT_DIR / "01_download_pbf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    return mod.download_all(
        config=config,
        output_dir=RAW_DIR,
        countries=countries,
        max_workers=kwargs.get("max_workers", 16),
    )


def step_extract(config: dict, countries: list[str] | None = None, railway_types: list[str] | None = None, **kwargs) -> list[dict]:
    """Step 2: Extract railway data from PBF files."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("extract_railway", SCRIPT_DIR / "02_extract_railway.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    return mod.extract_all(
        config=config,
        pbf_dir=RAW_DIR,
        output_dir=GEOJSON_DIR,
        countries=countries,
        railway_types=railway_types,
        max_workers=kwargs.get("extract_workers", 4),
    )


def step_convert(config: dict, countries: list[str] | None = None, **kwargs) -> list[dict]:
    """Step 3: Convert GeoJSON to KML."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("convert_to_kml", SCRIPT_DIR / "03_convert_to_kml.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    return mod.convert_all(
        config=config,
        geojson_dir=GEOJSON_DIR,
        kml_dir=KML_DIR,
        countries=countries,
        max_workers=kwargs.get("max_workers", 16),
    )


def step_upload(config: dict, countries: list[str] | None = None, bucket: str | None = None, **kwargs) -> list[dict]:
    """Step 4: Upload to Google Cloud Storage."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("upload_to_gcs", SCRIPT_DIR / "04_upload_to_gcs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    return mod.upload_all(
        config=config,
        kml_dir=KML_DIR,
        geojson_dir=GEOJSON_DIR,
        raw_dir=RAW_DIR,
        countries=countries,
        bucket_name=bucket,
        max_workers=kwargs.get("max_workers", 16),
    )


def step_overpass(config: dict, countries: list[str] | None = None, railway_types: list[str] | None = None, **kwargs) -> list[dict]:
    """Alternative: Use Overpass API instead of Geofabrik."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("overpass_fallback", SCRIPT_DIR / "overpass_fallback.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    return mod.query_all(
        config=config,
        output_dir=GEOJSON_DIR,
        countries=countries,
        railway_types=railway_types,
        max_workers=kwargs.get("extract_workers", 4),
    )


def run_pipeline(
    config: dict,
    steps: list[str] | None = None,
    countries: list[str] | None = None,
    railway_types: list[str] | None = None,
    bucket: str | None = None,
    use_overpass: bool = False,
    max_workers: int = 16,
    extract_workers: int = 4,
) -> dict:
    """
    Run the full pipeline or selected steps.
    
    Args:
        config: Loaded configuration dict
        steps: Steps to run ['download', 'extract', 'convert', 'upload'] (None = all)
        countries: Country codes to process (None = all)
        railway_types: Railway types to extract
        bucket: GCS bucket name override
        use_overpass: Use Overpass API instead of Geofabrik for extraction
    
    Returns:
        Pipeline results dict
    """
    if steps is None:
        if use_overpass:
            steps = ["overpass", "convert", "upload"]
        else:
            steps = ["download", "extract", "convert", "upload"]

    pipeline_start = time.time()
    results = {
        "started_at": datetime.now().isoformat(),
        "steps": {},
        "status": "running",
    }

    # Create data directories
    for d in [RAW_DIR, GEOJSON_DIR, KML_DIR, CACHE_DIR, REPORTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    step_runners = {
        "download": lambda: step_download(config, countries, max_workers=max_workers),
        "extract": lambda: step_extract(config, countries, railway_types, extract_workers=extract_workers),
        "convert": lambda: step_convert(config, countries, max_workers=max_workers),
        "upload": lambda: step_upload(config, countries, bucket, max_workers=max_workers),
        "overpass": lambda: step_overpass(config, countries, railway_types, extract_workers=extract_workers),
    }

    for step_name in steps:
        if step_name not in step_runners:
            console.print(f"[red]Unknown step: {step_name}[/red]")
            continue

        console.print(f"\n[bold magenta]{'='*60}[/bold magenta]")
        console.print(f"[bold magenta]STEP: {step_name.upper()}[/bold magenta]")
        console.print(f"[bold magenta]{'='*60}[/bold magenta]")

        step_start = time.time()

        try:
            step_result = step_runners[step_name]()
            results["steps"][step_name] = {
                "status": "completed",
                "duration_s": time.time() - step_start,
                "details": step_result,
            }
        except Exception as e:
            console.print(f"[red]Step '{step_name}' failed: {e}[/red]")
            results["steps"][step_name] = {
                "status": "failed",
                "duration_s": time.time() - step_start,
                "error": str(e),
            }
            # Don't continue if a critical step fails
            if step_name in ("download", "extract", "overpass"):
                console.print("[red]Critical step failed. Stopping pipeline.[/red]")
                break

    results["status"] = "completed"
    results["total_duration_s"] = time.time() - pipeline_start
    results["completed_at"] = datetime.now().isoformat()

    # Save pipeline report
    report_path = REPORTS_DIR / f"pipeline_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    # Print final summary
    console.print(f"\n[bold green]{'='*60}[/bold green]")
    console.print(f"[bold green]PIPELINE COMPLETE[/bold green]")
    console.print(f"[bold green]{'='*60}[/bold green]")
    console.print(f"[dim]Total duration: {results['total_duration_s']:.0f}s[/dim]")
    console.print(f"[dim]Report: {report_path}[/dim]")

    for step_name, step_data in results["steps"].items():
        status = step_data["status"]
        icon = "✓" if status == "completed" else "✗"
        color = "green" if status == "completed" else "red"
        console.print(f"  [{color}]{icon} {step_name}: {status} ({step_data['duration_s']:.0f}s)[/{color}]")

    return results


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ARTEMIS - Railway Data Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run the full pipeline for all countries
  python pipeline.py

  # Download and extract only UK and Italy
  python pipeline.py --countries uk italy --steps download extract

  # Convert and upload already-extracted data
  python pipeline.py --steps convert upload

  # Use Overpass API for small countries
  python pipeline.py --countries south_africa mexico --overpass

  # Specify custom railway types
  python pipeline.py --railway-types rail subway station
        """,
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=["download", "extract", "convert", "upload", "overpass"],
        help="Steps to run (default: all)",
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        help="Country codes to process (default: all)",
    )
    parser.add_argument(
        "--railway-types",
        nargs="+",
        help="Railway types to extract (default: from config)",
    )
    parser.add_argument(
        "--bucket",
        type=str,
        help="GCS bucket name (overrides config)",
    )
    parser.add_argument(
        "--overpass",
        action="store_true",
        help="Use Overpass API instead of Geofabrik (not recommended for large countries)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Config file path (default: {CONFIG_PATH})",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        help="Max workers for general I/O tasks like downloading/uploading (default: 16)",
    )
    parser.add_argument(
        "--extract-workers",
        type=int,
        default=4,
        help="Max workers for memory-intensive pyrosm extraction (default: 4)",
    )

    args = parser.parse_args()

    print_banner()

    config = load_config(args.config)

    results = run_pipeline(
        config=config,
        steps=args.steps,
        countries=args.countries,
        railway_types=args.railway_types,
        bucket=args.bucket,
        use_overpass=args.overpass,
        max_workers=args.max_workers,
        extract_workers=args.extract_workers,
    )

    # Exit with error if any critical step failed
    failed_steps = [
        name for name, data in results["steps"].items()
        if data["status"] == "failed"
    ]
    sys.exit(1 if failed_steps else 0)


if __name__ == "__main__":
    main()
