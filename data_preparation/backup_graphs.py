#!/usr/bin/env python3
"""
ARTEMIS - GraphML Backup to GCS
===============================
Uploads the compiled .graphml files to the GCS bucket so they are
preserved after the Vertex AI instance is destroyed.
"""

import os
import sys
import argparse
from pathlib import Path
from google.cloud import storage

def backup_graphs(bucket_name, graph_dir):
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    
    graph_path = Path(graph_dir)
    if not graph_path.exists():
        print(f"Error: Graph directory not found: {graph_path}")
        sys.exit(1)
        
    # Find all .graphml files
    graphml_files = list(graph_path.rglob("*.graphml"))
    
    if not graphml_files:
        print("No .graphml files found to upload.")
        sys.exit(0)
        
    print(f"Found {len(graphml_files)} graph files to upload to gs://{bucket_name}...")
    
    for local_file in graphml_files:
        # e.g., data/processed/graph/uk/uk_network.graphml -> processed/graph/uk/uk_network.graphml
        relative_path = local_file.relative_to(graph_path.parent)
        blob_name = f"processed/graph/{relative_path.name}" 
        # Actually, let's preserve the country folder structure:
        country_code = local_file.parent.name
        blob_name = f"processed/graph/{country_code}/{local_file.name}"
        
        blob = bucket.blob(blob_name)
        print(f"Uploading {local_file.name} -> gs://{bucket_name}/{blob_name} ({local_file.stat().st_size / 1e6:.1f} MB)...")
        
        # Resumable upload for large files
        blob.upload_from_filename(str(local_file), timeout=600)
        
    print("\n✓ Backup complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default="artemis-railway-data")
    args = parser.parse_args()
    
    base_dir = Path(__file__).resolve().parent.parent
    graph_dir = base_dir / "data" / "processed" / "graph"
    
    backup_graphs(args.bucket, graph_dir)
