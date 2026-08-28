#!/bin/bash
# ============================================================================
# ARTEMIS - Google Cloud Storage Bucket Setup
# ============================================================================
# Creates and configures the GCS bucket with structured directories,
# lifecycle rules, and appropriate access controls.
#
# Prerequisites:
#   - Google Cloud SDK (gcloud) installed and authenticated
#   - A GCP project with billing enabled
#   - Cloud Storage API enabled
#
# Usage:
#   chmod +x gcs_setup.sh
#   ./gcs_setup.sh                              # Use defaults
#   ./gcs_setup.sh --project my-project         # Specify project
#   ./gcs_setup.sh --bucket my-custom-bucket    # Custom bucket name
# ============================================================================

set -euo pipefail

# ======================== CONFIGURATION ========================

BUCKET_NAME="${BUCKET_NAME:-artemis-railway-data}"
LOCATION="${LOCATION:-us-central1}"           # Single region (Iowa)
STORAGE_CLASS="${STORAGE_CLASS:-STANDARD}"     # Frequently accessed data
UNIFORM_ACCESS="true"                          # Uniform bucket-level access

# ======================== PARSE ARGUMENTS ========================

while [[ $# -gt 0 ]]; do
    case $1 in
        --project)
            PROJECT_ID="$2"
            shift 2
            ;;
        --bucket)
            BUCKET_NAME="$2"
            shift 2
            ;;
        --location)
            LOCATION="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --project PROJECT_ID    GCP project ID"
            echo "  --bucket BUCKET_NAME    Bucket name (default: artemis-railway-data)"
            echo "  --location LOCATION     Bucket location (default: US)"
            echo ""
            echo "Location options:"
            echo "  US                Multi-region (US)"
            echo "  EU                Multi-region (Europe)"
            echo "  ASIA              Multi-region (Asia)"
            echo "  us-central1       Single region (Iowa)"
            echo "  europe-west1      Single region (Belgium)"
            echo "  asia-south1       Single region (Mumbai)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Default project ID
if [ -z "${PROJECT_ID:-}" ]; then
    PROJECT_ID="fine-ring-505908-e6"
fi

echo "============================================"
echo "ARTEMIS - GCS Bucket Setup"
echo "============================================"
echo "Project:       $PROJECT_ID"
echo "Bucket:        gs://$BUCKET_NAME"
echo "Location:      $LOCATION"
echo "Storage Class: $STORAGE_CLASS"
echo "============================================"
echo ""

# ======================== CREATE BUCKET ========================

echo ">>> Creating GCS bucket..."

# Check if bucket already exists
if gsutil ls -b "gs://$BUCKET_NAME" 2>/dev/null; then
    echo "    Bucket gs://$BUCKET_NAME already exists. Skipping creation."
else
    gsutil mb \
        -p "$PROJECT_ID" \
        -l "$LOCATION" \
        -c "$STORAGE_CLASS" \
        -b on \
        "gs://$BUCKET_NAME"
    echo "    ✓ Bucket created: gs://$BUCKET_NAME"
fi

# ======================== CONFIGURE BUCKET ========================

echo ""
echo ">>> Configuring bucket settings..."

# Enable versioning (useful for data updates)
echo "    Setting versioning..."
gsutil versioning set on "gs://$BUCKET_NAME"

# Set lifecycle rules
echo "    Setting lifecycle rules..."
cat > /tmp/artemis_lifecycle.json << 'LIFECYCLE_EOF'
{
  "lifecycle": {
    "rule": [
      {
        "action": {
          "type": "SetStorageClass",
          "storageClass": "NEARLINE"
        },
        "condition": {
          "age": 90,
          "matchesPrefix": ["raw/"],
          "matchesStorageClass": ["STANDARD"]
        }
      },
      {
        "action": {
          "type": "SetStorageClass",
          "storageClass": "COLDLINE"
        },
        "condition": {
          "age": 365,
          "matchesPrefix": ["raw/"],
          "matchesStorageClass": ["NEARLINE"]
        }
      },
      {
        "action": {
          "type": "Delete"
        },
        "condition": {
          "age": 30,
          "matchesPrefix": ["overpass_cache/"]
        }
      },
      {
        "action": {
          "type": "Delete"
        },
        "condition": {
          "numNewerVersions": 3
        }
      }
    ]
  }
}
LIFECYCLE_EOF

gsutil lifecycle set /tmp/artemis_lifecycle.json "gs://$BUCKET_NAME"
rm /tmp/artemis_lifecycle.json
echo "    ✓ Lifecycle rules applied"

# Set CORS (if accessing KML from web applications)
echo "    Setting CORS policy..."
cat > /tmp/artemis_cors.json << 'CORS_EOF'
[
  {
    "origin": ["*"],
    "method": ["GET", "HEAD"],
    "responseHeader": ["Content-Type", "Content-Disposition"],
    "maxAgeSeconds": 3600
  }
]
CORS_EOF

gsutil cors set /tmp/artemis_cors.json "gs://$BUCKET_NAME"
rm /tmp/artemis_cors.json
echo "    ✓ CORS policy applied"

# ======================== CREATE DIRECTORY STRUCTURE ========================

echo ""
echo ">>> Creating directory structure..."

# GCS doesn't have real directories, but we create placeholder objects
# to establish the structure visibly in the console

DIRECTORIES=(
    "raw/"
    "processed/geojson/"
    "processed/kml/"
    "metadata/"
    "overpass_cache/"
)

# Create country subdirectories
COUNTRIES=("us" "china" "russia" "canada" "germany" "australia" "argentina" "brazil" "france" "japan" "south_africa" "mexico" "uk" "italy")

for dir in "${DIRECTORIES[@]}"; do
    echo "    Creating gs://$BUCKET_NAME/$dir"
    echo "" | gsutil -q cp - "gs://$BUCKET_NAME/${dir}.keep" 2>/dev/null || true
done

# Create per-country subdirectories
for country in "${COUNTRIES[@]}"; do
    for subdir in "raw" "processed/geojson" "processed/kml"; do
        echo "" | gsutil -q cp - "gs://$BUCKET_NAME/${subdir}/${country}/.keep" 2>/dev/null || true
    done
done
echo "    ✓ Directory structure created"

# ======================== SET LABELS ========================

echo ""
echo ">>> Setting bucket labels..."
gsutil label ch \
    -l "project:artemis" \
    -l "component:data-storage" \
    -l "env:production" \
    "gs://$BUCKET_NAME"
echo "    ✓ Labels applied"

# ======================== SUMMARY ========================

echo ""
echo "============================================"
echo "GCS BUCKET READY"
echo "============================================"
echo ""
echo "Bucket URI: gs://$BUCKET_NAME"
echo ""
echo "Structure:"
echo "  gs://$BUCKET_NAME/"
echo "  ├── raw/                          # Downloaded PBF files"
echo "  │   ├── us/"
echo "  │   ├── china/"
echo "  │   ├── russia/"
echo "  │   ├── canada/"
echo "  │   ├── germany/"
echo "  │   ├── australia/"
echo "  │   ├── argentina/"
echo "  │   ├── brazil/"
echo "  │   ├── france/"
echo "  │   ├── japan/"
echo "  │   ├── south_africa/"
echo "  │   ├── mexico/"
echo "  │   ├── uk/"
echo "  │   └── italy/"
echo "  ├── processed/"
echo "  │   ├── geojson/                  # Intermediate GeoJSON"
echo "  │   │   └── <country>/"
echo "  │   └── kml/                      # Final KML files"
echo "  │       └── <country>/"
echo "  ├── metadata/                     # Reports & stats"
echo "  └── overpass_cache/               # Cached API responses"
echo ""
echo "Lifecycle rules:"
echo "  • Raw PBFs → Nearline after 90 days"
echo "  • Raw PBFs → Coldline after 365 days"
echo "  • Overpass cache → Deleted after 30 days"
echo "  • Max 3 versions per object"
echo ""
echo "Estimated monthly cost (after pipeline):"
echo "  • ~35 GB processed data: ~\$0.70/month (Standard)"
echo "  • ~35 GB raw PBFs (after 90 days): ~\$0.35/month (Nearline)"
echo ""
echo "CLI commands:"
echo "  List contents:     gsutil ls gs://$BUCKET_NAME/"
echo "  Download KML:      gsutil cp gs://$BUCKET_NAME/processed/kml/us/*.kml ."
echo "  Check size:        gsutil du -sh gs://$BUCKET_NAME/"
echo "============================================"
