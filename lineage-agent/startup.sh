#!/bin/bash
# startup.sh — Azure App Service startup for TiDy Lineage Agent
# Called via App Service startup command: bash startup.sh

cd /home/site/wwwroot

# Azure App Service sets PORT env var; default to 8000
export PORT=${PORT:-8000}

exec gunicorn \
    --bind "0.0.0.0:$PORT" \
    --workers 2 \
    --timeout 120 \
    --access-logfile '-' \
    --error-logfile '-' \
    web_app:app
