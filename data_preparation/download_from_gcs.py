import os
import argparse
from pathlib import Path
from google.cloud import storage

def download_bucket(bucket_name, dest_dir):
    """Downloads all files from a GCS bucket to a local directory."""
    print(f"Connecting to GCS bucket: {bucket_name}")
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs()
        
        download_count = 0
        for blob in blobs:
            # Create the local path
            local_path = Path(dest_dir) / blob.name
            
            # Create directories if they don't exist
            local_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Skip if it's a directory placeholder
            if blob.name.endswith('/'):
                continue
                
            print(f"Downloading {blob.name} to {local_path} ...")
            blob.download_to_filename(str(local_path))
            download_count += 1
            
        print(f"\nSuccessfully downloaded {download_count} files from '{bucket_name}' to '{dest_dir}'.")
        
    except Exception as e:
        print(f"Error downloading from bucket: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download all files from a GCS bucket.")
    parser.add_argument("--bucket", type=str, default="artemis-railway-data", help="GCS bucket name")
    parser.add_argument("--dest", type=str, default="data", help="Local destination directory")
    args = parser.parse_args()
    
    # Resolve absolute path for destination
    dest_dir = Path(__file__).resolve().parent.parent / args.dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    download_bucket(args.bucket, dest_dir)
