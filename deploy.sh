#!/bin/bash
set -e
cd "$(dirname "$0")"
source venv/bin/activate
git pull
pip install -r requirements.txt --quiet
sudo systemctl restart component-labels
echo ""
sudo systemctl status component-labels --no-pager
echo ""
echo "Deploy complete."
