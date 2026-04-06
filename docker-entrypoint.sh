#!/bin/bash
set -e

echo "==> Waiting for PostgreSQL to be ready..."
# Extract host and port from the sync DATABASE_URL for the health check
# Default to postgres:5432 if parsing fails
DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"

# Wait up to 30 seconds for Postgres to accept connections
for i in $(seq 1 30); do
    if curl -s "http://${DB_HOST}:${DB_PORT}" >/dev/null 2>&1 || \
       python -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.settimeout(2)
    s.connect(('${DB_HOST}', ${DB_PORT}))
    s.close()
    sys.exit(0)
except:
    sys.exit(1)
" 2>/dev/null; then
        echo "==> PostgreSQL is ready!"
        break
    fi
    echo "    Waiting for PostgreSQL... (${i}/30)"
    sleep 1
done

echo "==> Running Alembic migrations..."
alembic upgrade head

echo "==> Starting Uvicorn server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000