#!/usr/bin/env python3
"""
ARTEMIS - Step 01: Download PBF Files from Geofabrik
====================================================
Downloads OpenStreetMap PBF extracts for all configured countries.
Supports resume on interruption via HTTP Range headers.
Validates downloads against MD5 checksums provided by Geofabrik.
"""

import concurrent.futures
import hashlib
import json
import os
import sys
import time
import threading
from pathlib import Path

import requests
from tqdm import tqdm
from rich.console import Console
from rich.table import Table

console = Console()
print_lock = threading.Lock()

# ----- Configuration -----
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "countries.json"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "raw"

CHUNK_SIZE = 8 * 1024 * 1024  # 8MB download chunks
MAX_RETRIES = 5
RETRY_BACKOFF = 10  # seconds


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """Load country configuration from JSON."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_remote_file_size(url: str) -> int | None:
    """Get the file size from the remote server using a HEAD request."""
    try:
        response = requests.head(url, allow_redirects=True, timeout=30)
        if response.status_code == 200 and "Content-Length" in response.headers:
            return int(response.headers["Content-Length"])
    except requests.RequestException:
        pass
    return None


def verify_md5(file_path: Path, md5_url: str) -> bool:
    """Verify the MD5 checksum of a downloaded file against Geofabrik's .md5 file."""
    try:
        console.print(f"  [dim]Fetching MD5 checksum from Geofabrik...[/dim]")
        response = requests.get(md5_url, timeout=30)
        if response.status_code != 200:
            console.print(f"  [yellow]⚠ MD5 file not available (HTTP {response.status_code}), skipping verification[/yellow]")
            return True

        # Geofabrik MD5 format: "<hash>  <filename>"
        expected_md5 = response.text.strip().split()[0].lower()

        console.print(f"  [dim]Computing local MD5 (this may take a while for large files)...[/dim]")
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
                md5_hash.update(chunk)
        actual_md5 = md5_hash.hexdigest().lower()

        if actual_md5 == expected_md5:
            with print_lock:
                console.print(f"  [green]✓ MD5 verified: {actual_md5}[/green]")
            return True
        else:
            with print_lock:
                console.print(f"  [red]✗ MD5 mismatch! Expected: {expected_md5}, Got: {actual_md5}[/red]")
            return False
    except Exception as e:
        with print_lock:
            console.print(f"  [yellow]⚠ MD5 verification error: {e}[/yellow]")
        return True  # Don't block on verification errors


def download_pbf(
    country: dict,
    output_dir: Path,
    skip_existing: bool = True,
    verify: bool = True,
    pos: int = 0,
) -> dict:
    """
    Download a PBF file for a single country.
    
    Returns a status dict with keys: country, status, file_path, size_bytes, duration_s, error
    """
    country_name = country["name"]
    country_code = country["code"]
    url = country["geofabrik_url"]
    md5_url = country.get("geofabrik_md5_url", "")

    # Ensure output directory exists
    country_dir = output_dir / country_code
    country_dir.mkdir(parents=True, exist_ok=True)

    # Determine output file name from URL
    filename = url.split("/")[-1]
    output_path = country_dir / filename

    result = {
        "country": country_name,
        "code": country_code,
        "status": "unknown",
        "file_path": str(output_path),
        "size_bytes": 0,
        "duration_s": 0,
        "error": None,
    }

    start_time = time.time()

    # Check if file already exists and is complete
    if skip_existing and output_path.exists():
        local_size = output_path.stat().st_size
        remote_size = get_remote_file_size(url)

        if remote_size and local_size >= remote_size:
            console.print(f"  [green]✓ Already downloaded ({local_size / 1e9:.2f} GB)[/green]")
            result["status"] = "skipped"
            result["size_bytes"] = local_size
            return result

        if remote_size and local_size < remote_size:
            console.print(f"  [yellow]↻ Resuming download ({local_size / 1e9:.2f} / {remote_size / 1e9:.2f} GB)[/yellow]")
        else:
            local_size = 0  # Can't resume without knowing remote size
    else:
        local_size = 0

    # Download with resume support
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            headers = {}
            if local_size > 0:
                headers["Range"] = f"bytes={local_size}-"

            response = requests.get(url, headers=headers, stream=True, timeout=60)

            if response.status_code == 416:
                # Range not satisfiable - file is already complete
                console.print(f"  [green]✓ File is already complete[/green]")
                result["status"] = "skipped"
                result["size_bytes"] = local_size
                return result

            if response.status_code not in (200, 206):
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: {response.reason}"
                )

            # Determine total size for progress bar
            if response.status_code == 206:
                content_range = response.headers.get("Content-Range", "")
                if "/" in content_range:
                    total_size = int(content_range.split("/")[-1])
                else:
                    total_size = local_size + int(response.headers.get("Content-Length", 0))
                mode = "ab"  # Append for resume
            else:
                total_size = int(response.headers.get("Content-Length", 0))
                local_size = 0  # Reset - starting fresh
                mode = "wb"

            # Download with progress bar
            desc = f"  {country_name}"
            with (
                open(output_path, mode) as f,
                tqdm(
                    total=total_size,
                    initial=local_size,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=desc,
                    ncols=100,
                    position=pos,
                    leave=False,
                ) as pbar,
            ):
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))

            result["size_bytes"] = output_path.stat().st_size

            # Verify MD5 if requested
            if verify and md5_url:
                if not verify_md5(output_path, md5_url):
                    console.print(f"  [red]✗ MD5 verification failed, deleting and retrying...[/red]")
                    output_path.unlink(missing_ok=True)
                    local_size = 0
                    continue

            result["status"] = "downloaded"
            result["duration_s"] = time.time() - start_time
            return result

        except (requests.RequestException, IOError) as e:
            wait_time = RETRY_BACKOFF * attempt
            console.print(
                f"  [yellow]⚠ Attempt {attempt}/{MAX_RETRIES} failed: {e}[/yellow]"
            )
            if attempt < MAX_RETRIES:
                console.print(f"  [dim]Retrying in {wait_time}s...[/dim]")
                time.sleep(wait_time)
                # Update local_size for resume
                if output_path.exists():
                    local_size = output_path.stat().st_size
            else:
                result["status"] = "failed"
                result["error"] = str(e)
                result["duration_s"] = time.time() - start_time
                return result

    result["status"] = "failed"
    result["error"] = "Max retries exceeded"
    result["duration_s"] = time.time() - start_time
    return result


def download_all(
    config: dict,
    output_dir: Path = DEFAULT_DATA_DIR,
    countries: list[str] | None = None,
    skip_existing: bool = True,
    verify: bool = True,
    max_workers: int = 16,
) -> list[dict]:
    """
    Download PBF files for all (or selected) countries.
    
    Args:
        config: Loaded configuration dict
        output_dir: Base directory for downloaded files
        countries: List of country codes to download (None = all)
        skip_existing: Skip already downloaded files
        verify: Verify MD5 checksums after download
    
    Returns:
        List of status dicts for each country
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    country_list = config["countries"]
    if countries:
        country_list = [c for c in country_list if c["code"] in countries]

    total = len(country_list)
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]ARTEMIS - PBF Download Pipeline[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[dim]Countries: {total} | Output: {output_dir} | Workers: {max_workers}[/dim]\n")

    def process_country(args):
        i, country = args
        return download_pbf(country, output_dir, skip_existing, verify, pos=i)

    # Use ThreadPoolExecutor for concurrent downloads
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(process_country, (i, country)): country
            for i, country in enumerate(country_list, 1)
        }

        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)

            with print_lock:
                if result["status"] == "downloaded":
                    size_gb = result["size_bytes"] / 1e9
                    duration = result["duration_s"]
                    speed = result["size_bytes"] / duration / 1e6 if duration > 0 else 0
                    console.print(
                        f"  [green]✓ Complete: {result['country']} - {size_gb:.2f} GB in {duration:.0f}s "
                        f"({speed:.1f} MB/s)[/green]"
                    )
                elif result["status"] == "skipped":
                    console.print(f"  [cyan]→ Skipped: {result['country']} (already exists)[/cyan]")
                else:
                    console.print(f"  [red]✗ Failed: {result['country']} - {result['error']}[/red]")
    
    console.print("")

    # Print summary table
    _print_summary(results)

    # Save results to JSON
    results_path = output_dir / "download_report.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    console.print(f"\n[dim]Report saved to: {results_path}[/dim]")

    return results


def _print_summary(results: list[dict]) -> None:
    """Print a summary table of all download results."""
    table = Table(title="Download Summary")
    table.add_column("Country", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Size", justify="right")
    table.add_column("Duration", justify="right")

    for r in results:
        status_style = {
            "downloaded": "[green]✓ Downloaded[/green]",
            "skipped": "[cyan]→ Skipped[/cyan]",
            "failed": "[red]✗ Failed[/red]",
        }.get(r["status"], r["status"])

        size = f"{r['size_bytes'] / 1e9:.2f} GB" if r["size_bytes"] else "-"
        duration = f"{r['duration_s']:.0f}s" if r["duration_s"] else "-"

        table.add_row(r["country"], status_style, size, duration)

    console.print(table)

    # Totals
    downloaded = sum(1 for r in results if r["status"] == "downloaded")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = sum(1 for r in results if r["status"] == "failed")
    total_size = sum(r["size_bytes"] for r in results) / 1e9

    console.print(
        f"\n[bold]Total: {downloaded} downloaded, {skipped} skipped, "
        f"{failed} failed | {total_size:.2f} GB[/bold]"
    )


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ARTEMIS - Download PBF files from Geofabrik"
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        help="Country codes to download (e.g., us uk germany). Default: all",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Output directory (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Config file path (default: {CONFIG_PATH})",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="Re-download existing files",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip MD5 verification",
    )

    args = parser.parse_args()

    config = load_config(args.config)
    results = download_all(
        config=config,
        output_dir=args.output_dir,
        countries=args.countries,
        skip_existing=not args.no_skip,
        verify=not args.no_verify,
    )

    # Exit with error code if any downloads failed
    failed = sum(1 for r in results if r["status"] == "failed")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
