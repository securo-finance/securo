FROM python:3.14-slim

WORKDIR /app

# Install system deps for asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencies come from uv.lock (the source of truth — see scripts/lock.sh).
# The export never touches the lock (--frozen); pip then installs with hash
# checking, so the image only ever contains the exact audited versions. uv is
# removed afterwards — it is a build tool, not a runtime dependency.
# This layer is cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
# Space-separated optional extras to install (e.g. "s3" for STORAGE_PROVIDER=s3).
ARG BACKEND_EXTRAS=""
# renovate: datasource=pypi depName=uv
RUN pip install --no-cache-dir uv==0.13.0 \
    && uv export --frozen --no-emit-project $(for e in $BACKEND_EXTRAS; do printf -- '--extra %s ' "$e"; done) -o /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && pip uninstall -y uv \
    && rm /tmp/requirements.txt

COPY . .
# The project itself: deps are already satisfied above, so --no-deps keeps
# this layer from re-resolving anything outside the lock.
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
