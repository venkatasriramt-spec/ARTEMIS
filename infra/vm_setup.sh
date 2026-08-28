#!/bin/bash
# ============================================================================
# ARTEMIS - Google Cloud Vertex AI Notebook Instance Provisioning Script
# ============================================================================
# Creates and configures a Vertex AI User-Managed Notebook instance optimized
# for reinforcement learning (JupyterLab + ML frameworks pre-installed) and
# large-scale geospatial data processing (GDAL + pyrosm + simplekml).
#
# Prerequisites:
#   - Google Cloud SDK (gcloud) installed and authenticated
#   - A GCP project with billing enabled
#   - Notebooks API enabled (notebooks.googleapis.com)
#
# Usage:
#   chmod +x vm_setup.sh
#   ./vm_setup.sh                       # Use defaults
#   ./vm_setup.sh --project my-project  # Specify project
# ============================================================================

set -euo pipefail

# ======================== CONFIGURATION ========================

# ----- VM Settings -----
VM_NAME="${VM_NAME:-artemis-notebook}"
MACHINE_TYPE="${MACHINE_TYPE:-n2d-highmem-16}"   # 16 vCPUs, 128GB RAM (Fits GCP quota of 16 N2D CPUS)
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-200}"         # Size in GB (integer) - Reduced to 200GB to fit GCP Quotas
BOOT_DISK_TYPE="PD_SSD"                         # PD_SSD or PD_STANDARD

# ----- Labels -----
LABELS="project=artemis,component=notebook,env=production"

# ======================== PARSE ARGUMENTS ========================

while [[ $# -gt 0 ]]; do
    case $1 in
        --project)
            PROJECT_ID="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --zone)
            ZONE="$2"
            shift 2
            ;;
        --machine-type)
            MACHINE_TYPE="$2"
            shift 2
            ;;
        --disk-size)
            BOOT_DISK_SIZE="$2"
            shift 2
            ;;
        --name)
            VM_NAME="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --project PROJECT_ID    GCP project ID"
            echo "  --region REGION         GCP region to select zone from (default: us-central1)"
            echo "  --zone ZONE             GCP zone (default: auto-select based on availability)"
            echo "  --machine-type TYPE      Machine type (default: e2-highmem-8)"
            echo "  --disk-size SIZE         Boot disk size in GB (default: 500)"
            echo "  --name NAME              Notebook instance name (default: artemis-notebook)"
            echo ""
            echo "Recommended machine types:"
            echo "  n2d-highmem-32   32 vCPUs, 256GB RAM - Recommended for Phase 2 (Network Graph/RL)"
            echo "  n2d-highmem-16   16 vCPUs, 128GB RAM - Recommended for Phase 1 (Data pipeline)"
            echo "  e2-standard-4    4 vCPUs, 16GB RAM   - Development"
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
echo "ARTEMIS - Vertex AI Notebook Provisioning"
echo "============================================"
echo "Project:      $PROJECT_ID"
echo "Name:         $VM_NAME"
echo "Machine Type: $MACHINE_TYPE"
if [ -n "$ZONE" ]; then
    echo "Zone:         $ZONE"
else
    echo "Region:       $REGION (Zone auto-selected)"
fi
echo "Disk Size:    ${BOOT_DISK_SIZE} GB ($BOOT_DISK_TYPE)"
echo "============================================"
echo ""

# Enable the Notebooks API
echo ">>> Enabling Notebooks API (notebooks.googleapis.com)..."
gcloud services enable notebooks.googleapis.com --project="$PROJECT_ID"

# ======================== CREATE STARTUP SCRIPT ========================

# Startup script to install dependencies inside the Vertex AI environment
STARTUP_SCRIPT='#!/bin/bash
set -e

# Log output
exec > /var/log/artemis-setup.log 2>&1
echo "=== ARTEMIS Vertex AI Setup Started at $(date) ==="

# System updates
apt-get update -y

# Install system geospatial libraries
apt-get install -y \
    gdal-bin \
    libgdal-dev \
    libspatialindex-dev \
    libprotobuf-dev \
    protobuf-compiler \
    osmium-tool

# Install Python packages directly into Vertex AI default Python environment
/opt/conda/bin/pip install \
    pyrosm>=0.6.1 \
    geopandas>=0.14.0 \
    fiona>=1.9.0 \
    shapely>=2.0.0 \
    pyproj>=3.6.0 \
    simplekml>=1.3.6 \
    fastkml>=0.12 \
    osmium>=3.7.0 \
    overpy>=0.7 \
    requests>=2.31.0 \
    tqdm>=4.66.0 \
    google-cloud-storage>=2.14.0 \
    click>=8.1.0 \
    rich>=13.7.0 \
    networkx>=3.1 \
    scipy>=1.11.0 \
    fastapi>=0.100.0 \
    uvicorn>=0.23.0

# Create project workspace directory inside /home/jupyter (default JupyterLab folder)
mkdir -p /home/jupyter/artemis/data/raw
mkdir -p /home/jupyter/artemis/data/processed/geojson
mkdir -p /home/jupyter/artemis/data/processed/kml
mkdir -p /home/jupyter/artemis/data/overpass_cache
mkdir -p /home/jupyter/artemis/data/reports

# Ensure correct permissions for the jupyter user
chown -R jupyter:jupyter /home/jupyter/artemis

echo "=== ARTEMIS Vertex AI Setup Completed at $(date) ==="
'

# ======================== CREATE NOTEBOOK INSTANCE ========================

if [ -z "$ZONE" ]; then
    echo ">>> Discovering available zones in $REGION..."
    AVAILABLE_ZONES=$(gcloud compute zones list --filter="region:($REGION) AND status:UP" --format="value(name)")
    ZONES_TO_TRY=($AVAILABLE_ZONES)
else
    ZONES_TO_TRY=("$ZONE")
fi

SUCCESS=false
for TRY_ZONE in "${ZONES_TO_TRY[@]}"; do
    echo ">>> Attempting to create Notebook Instance in zone: $TRY_ZONE..."
    
    if gcloud workbench instances create "$VM_NAME" \
        --project="$PROJECT_ID" \
        --location="$TRY_ZONE" \
        --machine-type="$MACHINE_TYPE" \
        --vm-image-project="cloud-notebooks-managed" \
        --vm-image-family="workbench-instances" \
        --boot-disk-size="$BOOT_DISK_SIZE" \
        --boot-disk-type="$BOOT_DISK_TYPE" \
        --metadata=startup-script="$STARTUP_SCRIPT"; then
        
        echo ""
        echo ">>> Notebook Instance created successfully in $TRY_ZONE!"
        ZONE="$TRY_ZONE"
        SUCCESS=true
        break
    else
        echo ">>> Failed to create Notebook Instance in $TRY_ZONE. Trying next zone if available..."
        echo ""
    fi
done

if [ "$SUCCESS" = false ]; then
    echo ">>> ERROR: Failed to create Vertex AI Notebook in all attempted zones."
    exit 1
fi

# ======================== WAIT FOR STARTUP ========================

echo ">>> Waiting for Notebook startup script to complete..."
echo "    (This may take 2-4 minutes for geospatial installation)"
echo ""

echo ">>> NOTE: The background startup script is now running on the instance."
echo ">>> It is installing geospatial libraries (GDAL, pyrosm) and python packages."
echo ">>> This process takes approximately 3-5 minutes to complete."
echo ">>> You can safely proceed to the next steps after waiting a few minutes."

echo ""
echo "=========================================================================="
echo "VERTEX AI NOTEBOOK READY"
echo "=========================================================================="
echo ""
echo "Access JupyterLab UI (No RDP/SSH needed!):"
echo "  1. Open the Google Cloud Console."
echo "  2. Navigate to Vertex AI > Workbench > Instances."
echo "  3. Click 'OPEN JUPYTERLAB' next to: $VM_NAME"
echo ""
echo "Transfer project files to notebook:"
echo "  gcloud compute scp --recurse ./scripts ./config $VM_NAME:/home/jupyter/artemis/ --zone=$ZONE"
echo ""
echo "Run the pipeline:"
echo "  1. In JupyterLab, open a Terminal window."
echo "  2. Run: cd /home/jupyter/artemis"
echo "  3. Run: python scripts/pipeline.py"
echo ""
echo "Stop Notebook when done (to avoid charges):"
echo "  gcloud workbench instances stop $VM_NAME --location=$ZONE --project=$PROJECT_ID"
echo ""
echo "Start Notebook again:"
echo "  gcloud workbench instances start $VM_NAME --location=$ZONE --project=$PROJECT_ID"
echo ""
echo "Delete Notebook when finished:"
echo "  gcloud workbench instances delete $VM_NAME --location=$ZONE --project=$PROJECT_ID"
echo "=========================================================================="
