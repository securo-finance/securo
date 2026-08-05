FROM python:3.13-slim

WORKDIR /app

# Install system deps for asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencies come from the lock export (see scripts/lock.sh), installed with
# hash checking so the image only ever contains the exact audited versions.
# This layer is cached until requirements.txt itself changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY . .
# The project itself: deps are already satisfied above, so --no-deps keeps
# this layer from re-resolving anything outside the lock.
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
