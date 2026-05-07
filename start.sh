#!/bin/bash

# Start Celery worker in background
# We use concurrency=2 to avoid memory overload on the 16GB free tier
celery -A tasks worker --loglevel=info --concurrency=2 -B &
CELERY_PID=$!

# Trap SIGTERM and SIGINT for graceful shutdown
trap "kill $CELERY_PID; exit 0" SIGTERM SIGINT

# Start Gunicorn in foreground
# Hugging Face Spaces expects the app to listen on port 7860
gunicorn -w 4 -b 0.0.0.0:${PORT:-7860} app_new:app
