FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# Export lockfile → requirements for reproducible installs and layer caching
COPY pyproject.toml uv.lock ./
RUN uv export --no-hashes -o /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copy application source
COPY dns_monitor/ dns_monitor/
COPY main.py serve.py ./

# Data directory for SQLite DB and optional GeoIP files
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# Default: web server. Override CMD in docker-compose for the monitor process.
CMD ["python", "serve.py", "--db", "/data/monitor.db", "--host", "0.0.0.0", "--port", "8000"]
