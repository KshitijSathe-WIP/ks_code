web: gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 120 --worker-class uvicorn.workers.UvicornWorker --access-logfile '-' --error-logfile '-' src.api.main:app
