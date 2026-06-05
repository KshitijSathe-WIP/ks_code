#!/bin/bash
# startup.sh — Azure App Service startup for TiDy Lineage Agent
# Startup command in Azure: bash /home/site/wwwroot/startup.sh

cd /home/site/wwwroot

export PORT=${PORT:-8000}

exec gunicorn \
    --bind "0.0.0.0:$PORT" \
    --workers 2 \
    --timeout 120 \
    --access-logfile '-' \
    --error-logfile '-' \
    web_app:app
