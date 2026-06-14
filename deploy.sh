#!/bin/bash
set -e
cd ~/component-labels
source venv/bin/activate
git pull
pip install -r requirements.txt --quiet
sudo systemctl restart component-labels
echo ""
sudo systemctl status component-labels --no-pager
echo ""
echo "Deploy complete."
