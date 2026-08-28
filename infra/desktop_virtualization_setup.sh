#!/bin/bash
# ==============================================================================
# ARTEMIS - Desktop Virtualization Setup Script (Ubuntu 22.04 LTS)
# ==============================================================================
# This script provisions a headless Ubuntu server into a fully accessible
# Virtual Desktop using XFCE and XRDP. It includes Python 3.10+, Google Chrome,
# geospatial libraries, and sets up the environment for ARTEMIS.
#
# ==============================================================================
# RECOMMENDED GCP VM SPECIFICATIONS:
#   Operating System: Ubuntu 22.04 LTS (Jammy)
#   Machine Type:     e2-standard-4 (4 vCPUs, 16 GB RAM) or higher
#                     (Requires decent RAM for loading large GraphML files)
#   Disk Space:       50 GB+ SSD Persistent Disk
#   Firewall Rules:   Allow tcp:3389 (for RDP access)
# ==============================================================================
#
# Usage: 
#   sudo bash desktop_virtualization_setup.sh
# ==============================================================================

set -e

echo "Starting Desktop Virtualization Setup for ARTEMIS..."

# 1. Update system and install basic utilities
echo ">>> Updating system..."
apt-get update -y && apt-get upgrade -y
apt-get install -y wget curl git build-essential software-properties-common apt-transport-https

# 2. Install Optimized Linux Desktop Environment (XFCE) and XRDP
# XFCE is lightweight and highly optimized for virtualization.
echo ">>> Installing XFCE Desktop Environment and XRDP..."
DEBIAN_FRONTEND=noninteractive apt-get install -y xfce4 xfce4-goodies xrdp
systemctl enable xrdp
# Configure XRDP to use XFCE
echo "xfce4-session" > /etc/skel/.xsession
chmod +x /etc/skel/.xsession
# Apply to current user if running as root for a specific user, otherwise applies to new users.
cp /etc/skel/.xsession /root/.xsession || true

# Fix xrdp ssl cert permissions
adduser xrdp ssl-cert || true
systemctl restart xrdp

# 3. Install Python 3.10+ and Geospatial System Dependencies
echo ">>> Installing Python and Geospatial Libraries (GDAL/PROJ)..."
add-apt-repository ppa:ubuntugis/ppa -y
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3.10 \
    python3.10-venv \
    python3.10-dev \
    python3-pip \
    gdal-bin \
    libgdal-dev \
    osmium-tool

# 4. Install Google Chrome (Web Browser)
echo ">>> Installing Google Chrome..."
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y google-chrome-stable

# 5. Install Antigravity IDE
echo ">>> Installing Antigravity IDE..."
# Add Google's Antigravity repository and install the standalone IDE
wget -qO- https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/google-cloud.gpg
echo "deb [signed-by=/usr/share/keyrings/google-cloud.gpg] https://packages.cloud.google.com/apt antigravity-ide main" > /etc/apt/sources.list.d/antigravity-ide.list
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y antigravity-ide

# 6. Setup Python Environment for ARTEMIS
echo ">>> Setting up Python virtual environment for ARTEMIS..."
# Assuming we do this in /opt/artemis or user's home
WORKDIR="/opt/artemis"
mkdir -p $WORKDIR
cd $WORKDIR

python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# Install required packages for ARTEMIS (Phases 1-4)
echo ">>> Installing ARTEMIS Python dependencies..."
pip install \
    geopandas \
    shapely \
    pyrosm \
    networkx \
    scipy \
    fastapi \
    uvicorn \
    pydantic \
    simplekml \
    fastkml

chown -R $SUDO_USER:$SUDO_USER $WORKDIR 2>/dev/null || true

# 7. Final Instructions
echo "========================================================================"
echo "✅ Setup Complete!"
echo "========================================================================"
echo "You can now connect to this machine using a Remote Desktop (RDP) client:"
echo "  - Windows: Open 'Remote Desktop Connection' and enter the VM's IP address."
echo "  - Mac: Use 'Microsoft Remote Desktop' from the App Store."
echo "  - Linux: Use 'Remmina' or 'Vinagre'."
echo ""
echo "Once logged in:"
echo "1. Open Google Chrome from the applications menu."
echo "2. Open the IDE (Code) to edit ARTEMIS scripts."
echo "3. Run your FastAPI visualization server locally and view it directly"
echo "   in Chrome at http://127.0.0.1:8000 (no proxy needed!)."
echo "========================================================================"
