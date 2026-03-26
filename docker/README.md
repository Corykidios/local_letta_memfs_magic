# Docker Git MemFS Server

Docker-adapted version of git-memfs-server for running as a sidecar container.

## Files

- `git-memfs-server.py` - Modified server that listens on 0.0.0.0 with configurable env vars
- `Dockerfile` - Container image with git and Python 3.11
- `healthcheck.py` - Simple HTTP health check for Docker

## Environment Variables

- `MEMFS_PORT` (default: 8285) - Port to listen on
- `MEMFS_BASE_PATH` (default: /data/memfs/repository) - Base path for bare repos
- `MEMFS_DEFAULT_ORG` (default: default-org) - Default org ID

## Key Differences from Original

1. Binds to `0.0.0.0` instead of `127.0.0.1` (container networking)
2. Uses `ThreadingHTTPServer` for concurrent requests
3. All paths configurable via environment variables
4. Structured JSON logging with timestamps
5. Health check endpoint at `/health`

## Docker Compose Integration

```yaml
services:
  memfs:
    build: ./local_letta_memfs_magic/docker
    volumes:
      - letta_memfs_data:/data/memfs/repository
    environment:
      - MEMFS_PORT=8285
      - MEMFS_DEFAULT_ORG=default-org
    networks:
      - letta-network
    healthcheck:
      test: ["CMD", "python", "/app/healthcheck.py"]
      interval: 30s
      timeout: 5s
      retries: 3
```

The Letta server should set `LETTA_MEMFS_SERVICE_URL=http://memfs:8285` to use this sidecar.

## Full Setup

See [`docker-compose.example.yaml`](../docker-compose.example.yaml) in the repo root
for a complete working Compose configuration, and
[`docs/DOCKER-DEPLOYMENT.md`](../docs/DOCKER-DEPLOYMENT.md) for the full deployment guide.

## Required Override

The Letta container also needs `memfs_client_base.py` overridden to fix a
`redis_client` kwarg mismatch. See [`overrides/`](../overrides/) for the
patched file, and mount it read-only:

```yaml
volumes:
  - ./overrides/letta/services/memory_repo/memfs_client_base.py:/app/letta/services/memory_repo/memfs_client_base.py:ro
```
