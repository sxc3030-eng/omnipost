#!/bin/bash
# OmniPost v1.1 - Install on i5 (24/7 service)
# Usage: bash install_i5.sh

set -e

INSTALL_DIR=/srv/omnipost
SERVICE_NAME=omnipost

echo "[1/6] Installing system deps..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip ffmpeg fonts-dejavu-core git

echo "[2/6] Cloning/updating repo..."
sudo mkdir -p $INSTALL_DIR
sudo chown -R $USER:$USER $INSTALL_DIR
if [ -d "$INSTALL_DIR/.git" ]; then
  cd $INSTALL_DIR && git pull
else
  git clone https://github.com/sxc3030-eng/omnipost.git $INSTALL_DIR
  cd $INSTALL_DIR
fi

echo "[3/6] Python deps..."
pip3 install --user -r requirements.txt

echo "[4/6] Pipeline directories..."
mkdir -p $INSTALL_DIR/pipeline/{created,converted,approved,published,failed}
mkdir -p $INSTALL_DIR/media

echo "[5/6] systemd service..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=OmniPost v1.1 - Social cross-poster + GeniA pipeline
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/omnipost.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/omnipost.log
StandardError=append:/var/log/omnipost.log

[Install]
WantedBy=multi-user.target
EOF

sudo touch /var/log/omnipost.log
sudo chown $USER:$USER /var/log/omnipost.log

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

echo "[6/6] Done."
echo ""
echo "✓ OmniPost installé."
echo "  Status:  sudo systemctl status omnipost"
echo "  Logs:    tail -f /var/log/omnipost.log"
echo "  Stop:    sudo systemctl stop omnipost"
echo ""
echo "WebSocket:    ws://localhost:8860"
echo "OAuth:        http://localhost:8861"
echo "Pipeline:     $INSTALL_DIR/pipeline/"
echo ""
echo "Pour activer GeniA pipeline, edit $INSTALL_DIR/omnipost_settings.json:"
echo '  "genia": { "enabled": true, ... }'
echo "Puis: sudo systemctl restart omnipost"
