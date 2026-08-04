#!/usr/bin/env bash

# Force the script to exit immediately if any command fails
set -e

# Run database migrations.
# This startup script is necessary for free-tier PaaS (Platform as a Service) 
# environments that do not support automated pre-deploy migration hooks.
echo "Applying Alembic migrations..."
alembic upgrade head

# Start the FastAPI server
echo "Starting Uvicorn..."
uvicorn app.main:app --host 0.0.0.0 --port 8000
