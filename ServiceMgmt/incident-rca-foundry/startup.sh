#!/bin/bash
# startup.sh — Azure App Service startup for Incident RCA Evidence API
# Startup command in Azure: bash /home/site/wwwroot/startup.sh

cd /home/site/wwwroot

export PORT=${PORT:-8000}

exec gunicorn \
    --bind "0.0.0.0:$PORT" \
    --workers 2 \
    --timeout 120 \
    --worker-class uvicorn.workers.UvicornWorker \
    --access-logfile '-' \
    --error-logfile '-' \
    src.api.main:app
