# Component Label System

Automated electronic component label generator for Avery 45×45-S label sheets.
Give it a part number, get a print-ready PDF label.

## Features
- Looks up component data via Nexar (Octopart) API
- Downloads and parses datasheets to extract pinout diagrams
- Generates colour-coded 45×45mm labels per component type
- Caches all data locally — no repeat API calls
- Web UI for browsing, editing, and building print jobs
- CLI for automation and batch processing

## Requirements
- Python 3.11+
- Linux (Ubuntu/Debian recommended)

## Installation

### 1. System dependencies
sudo apt update
sudo apt install python3-pip weasyprint

### 2. Clone and install
git clone https://github.com/<you>/component-labels.git
cd component-labels
pip install -r requirements.txt

### 3. Configure
cp .env.example .env
# Edit .env and fill in your Nexar credentials and server IP

### 4. Run
# Web UI
python main.py web

# CLI — add a part
python main.py add NE555

# CLI — batch from file
python main.py add --file parts.txt

# CLI — generate print PDF
python main.py print NE555 BC547 --start 1 --output labels.pdf

## Docker (future)
A Dockerfile and docker-compose.yml will be added once the
core system is stable.

## Adding a new component type
Add one entry to COMPONENT_TYPES in config.py. No other
changes required.
