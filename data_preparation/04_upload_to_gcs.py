#!/usr/bin/env python3
"""
ARTEMIS - Step 04: Upload KML Files to Google Cloud Storage
============================================================
Uploads processed KML (and optionally GeoJSON) files to a GCS bucket
with structured paths, parallel uploads, and metadata tagging.
"""

import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.table import Table
from tqdm import tqdm

console = Console()
print_lock = threading.Lock()

# ----- Configuration -----
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "countries.json"
DEFAULT_KML_DIR = PROJECT_ROOT / "data" / "processed" / "kml"
DEFAULT_GEOJSON_DIR = PROJECT_ROOT / "data" / "processed" / "geojson"
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """Load country configuration from JSON."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_gcs_client():
    """Get an authenticated GCS client."""
    try:
        from google.cloud import storage
        return storage.Client()
    except ImportError:
        console.print("[red]Error: google-cloud-storage not installed.[/red]")
        console.print("[dim]Run: pip install google-cloud-storage[/dim]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error creating GCS client: {e}[/red]")
        console.print("[dim]Ensure GOOGLE_APPLICATION_CREDENTIALS is set or run: gcloud auth application-default login[/dim]")
        sys.exit(1)


def ensure_bucket(client, bucket_name: str, location: str = "US") -> object:
    """Get or create a GCS bucket."""
    from google.cloud import storage as gcs
    from google.api_core.exceptions import Conflict, NotFound

    try:
        bucket = client.get_bucket(bucket_name)
        console.print(f"[green]✓ Using existing bucket: gs://{bucket_name}[/green]")
        return bucket
    except NotFound:
        console.print(f"[yellow]Creating bucket: gs://{bucket_name}[/yellow]")
        try:
            bucket = client.create_bucket(bucket_name, location=location)
            console.print(f"[green]✓ Bucket created: gs://{bucket_name}[/green]")
            return bucket
        except Conflict:
            console.print(f"[red]✗ Bucket name '{bucket_name}' is already taken globally[/red]")
            sys.exit(1)


def upload_file(
    bucket,
    local_path: Path,
    gcs_path: str,
    content_type: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Upload a single file to GCS.
    
    Returns:
        Status dict with upload results
    """
    result = {
        "local_path": str(local_path),
        "gcs_path": gcs_path,
        "size_bytes": 0,
        "status": "unknown",
        "error": None,
    }

    try:
        blob = bucket.blob(gcs_path)

        # Set content type
        if content_type:
            blob.content_type = content_type
        elif local_path.suffix == ".kml":
            blob.content_type = "application/vnd.google-earth.kml+xml"
        elif local_path.suffix == ".geojson":
            blob.content_type = "application/geo+json"
        elif local_path.suffix == ".json":
            blob.content_type = "application/json"
        elif local_path.suffix == ".pbf":
            blob.content_type = "application/octet-stream"

        # Set custom metadata
        if metadata:
            blob.metadata = metadata

        # Upload
        file_size = local_path.stat().st_size
        result["size_bytes"] = file_size

        # Use resumable upload for files > 5MB
        if file_size > 5 * 1024 * 1024:
            blob.upload_from_filename(str(local_path), timeout=600)
        else:
            blob.upload_from_filename(str(local_path), timeout=120)

        result["status"] = "uploaded"

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)

    return result


def upload_country_kml(
    bucket,
    country: dict,
    kml_dir: Path,
) -> list[dict]:
    """Upload all KML files for a country."""
    country_code = country["code"]
    country_kml_dir = kml_dir / country_code
    results = []

    if not country_kml_dir.exists():
        return [{
            "local_path": str(country_kml_dir),
            "gcs_path": "",
            "status": "failed",
            "error": "Directory not found",
        }]

    kml_files = list(country_kml_dir.glob("*.kml"))
    if not kml_files:
        return [{
            "local_path": str(country_kml_dir),
            "gcs_path": "",
            "status": "failed",
            "error": "No KML files found",
        }]

    metadata = {
        "country": country["name"],
        "country_code": country_code,
        "source": "geofabrik",
        "project": "ARTEMIS",
    }

    for kml_file in kml_files:
        gcs_path = f"processed/kml/{country_code}/{kml_file.name}"
        result = upload_file(bucket, kml_file, gcs_path, metadata=metadata)
        results.append(result)

    return results


def upload_country_geojson(
    bucket,
    country: dict,
    geojson_dir: Path,
) -> list[dict]:
    """Upload all GeoJSON files for a country."""
    country_code = country["code"]
    country_geojson_dir = geojson_dir / country_code
    results = []

    if not country_geojson_dir.exists():
        return []

    geojson_files = list(country_geojson_dir.glob("*.geojson"))

    metadata = {
        "country": country["name"],
        "country_code": country_code,
        "source": "geofabrik",
        "project": "ARTEMIS",
    }

    for gj_file in geojson_files:
        gcs_path = f"processed/geojson/{country_code}/{gj_file.name}"
        result = upload_file(bucket, gj_file, gcs_path, metadata=metadata)
        results.append(result)

    return results


def upload_country_raw(
    bucket,
    country: dict,
    raw_dir: Path,
) -> list[dict]:
    """Upload raw PBF files for a country."""
    country_code = country["code"]
    country_raw_dir = raw_dir / country_code
    results = []

    if not country_raw_dir.exists():
        return []

    pbf_files = list(country_raw_dir.glob("*.osm.pbf"))

    for pbf_file in pbf_files:
        gcs_path = f"raw/{country_code}/{pbf_file.name}"
        result = upload_file(bucket, pbf_file, gcs_path)
        results.append(result)

    return results


def upload_all(
    config: dict,
    kml_dir: Path = DEFAULT_KML_DIR,
    geojson_dir: Path = DEFAULT_GEOJSON_DIR,
    raw_dir: Path = DEFAULT_RAW_DIR,
    countries: list[str] | None = None,
    include_geojson: bool = True,
    include_raw: bool = False,
    bucket_name: str | None = None,
    max_workers: int = 16,
) -> list[dict]:
    """
    Upload all processed files to GCS.
    
    Args:
        config: Loaded configuration dict
        kml_dir: Directory containing KML files
        geojson_dir: Directory containing GeoJSON files
        raw_dir: Directory containing raw PBF files
        countries: Country codes to upload (None = all)
        include_geojson: Also upload GeoJSON files
        include_raw: Also upload raw PBF files (large!)
        bucket_name: Override bucket name from config
    """
    # Initialize GCS client
    client = get_gcs_client()

    gcs_config = config.get("gcs_settings", {})
    if not bucket_name:
        bucket_name = gcs_config.get("bucket_name", "artemis-railway-data")
    location = gcs_config.get("region", "US")

    bucket = ensure_bucket(client, bucket_name, location)

    country_list = config["countries"]
    if countries:
        country_list = [c for c in country_list if c["code"] in countries]

    total = len(country_list)
    all_results = []

    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]ARTEMIS - Upload to Google Cloud Storage[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[dim]Bucket: gs://{bucket_name} | Countries: {total} | Workers: {max_workers}[/dim]")
    console.print(f"[dim]KML: ✓ | GeoJSON: {'✓' if include_geojson else '✗'} | Raw PBF: {'✓' if include_raw else '✗'}[/dim]\n")

    def process_country(country):
        start = time.time()
        results = []

        # Upload KML files
        kml_results = upload_country_kml(bucket, country, kml_dir)
        results.extend(kml_results)

        # Upload GeoJSON files
        if include_geojson:
            gj_results = upload_country_geojson(bucket, country, geojson_dir)
            results.extend(gj_results)

        # Upload raw PBF files
        if include_raw:
            raw_results = upload_country_raw(bucket, country, raw_dir)
            results.extend(raw_results)
            
        uploaded = sum(1 for r in results if r["status"] == "uploaded")
        total_size = sum(r.get("size_bytes", 0) for r in results if r["status"] == "uploaded")
        elapsed = time.time() - start
        
        return country["name"], results, uploaded, total_size, elapsed

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_country, country): country for country in country_list}
        for future in tqdm(as_completed(futures), total=total, desc="Uploading to GCS", unit="country"):
            country_name, results, uploaded, total_size, elapsed = future.result()
            all_results.extend(results)
    
    console.print("")

    # Upload metadata/reports
    _upload_reports(bucket, kml_dir, geojson_dir)

    # Print summary
    _print_summary(all_results, bucket_name)

    return all_results


def _upload_reports(bucket, kml_dir: Path, geojson_dir: Path) -> None:
    """Upload any report JSON files to the metadata folder."""
    report_files = []
    for report_dir in [kml_dir, geojson_dir]:
        report_files.extend(report_dir.glob("*_report.json"))

    for report in report_files:
        gcs_path = f"metadata/{report.name}"
        upload_file(bucket, report, gcs_path)
        console.print(f"[dim]  ↑ Report: {report.name} → gs://.../{gcs_path}[/dim]")


def _print_summary(results: list[dict], bucket_name: str) -> None:
    """Print upload summary."""
    uploaded = sum(1 for r in results if r["status"] == "uploaded")
    failed = sum(1 for r in results if r["status"] == "failed")
    total_size = sum(r.get("size_bytes", 0) for r in results if r["status"] == "uploaded")

    console.print(f"\n[bold]Upload Summary:[/bold]")
    console.print(f"  Uploaded: {uploaded} files ({total_size / 1e6:.1f} MB)")
    console.print(f"  Failed: {failed} files")
    console.print(f"  Bucket: gs://{bucket_name}/")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ARTEMIS - Upload files to Google Cloud Storage"
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        help="Country codes to upload (default: all)",
    )
    parser.add_argument(
        "--bucket",
        type=str,
        help="GCS bucket name (overrides config)",
    )
    parser.add_argument(
        "--kml-dir",
        type=Path,
        default=DEFAULT_KML_DIR,
    )
    parser.add_argument(
        "--geojson-dir",
        type=Path,
        default=DEFAULT_GEOJSON_DIR,
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
    )
    parser.add_argument(
        "--include-geojson",
        action="store_true",
        default=True,
        help="Also upload GeoJSON files (default: True)",
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        default=False,
        help="Also upload raw PBF files (WARNING: very large)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
    )

    args = parser.parse_args()

    config = load_config(args.config)
    upload_all(
        config=config,
        kml_dir=args.kml_dir,
        geojson_dir=args.geojson_dir,
        raw_dir=args.raw_dir,
        countries=args.countries,
        include_geojson=args.include_geojson,
        include_raw=args.include_raw,
        bucket_name=args.bucket,
    )


if __name__ == "__main__":
    main()
