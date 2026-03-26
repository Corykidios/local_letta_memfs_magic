FROM python:3.11-slim-bookworm

# Install git for git http-backend and git init
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Create data directory for memfs repositories
RUN mkdir -p /data/memfs/repository

# Copy server and healthcheck scripts
COPY git-memfs-server.py /app/git-memfs-server.py
COPY healthcheck.py /app/healthcheck.py

WORKDIR /app

# Expose the memfs service port
EXPOSE 8285

# Health check using the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python /app/healthcheck.py

# Run the git-memfs-server
CMD ["python", "/app/git-memfs-server.py"]
