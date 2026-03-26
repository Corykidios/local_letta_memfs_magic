#!/usr/bin/env python3
"""
healthcheck.py — Docker healthcheck for git-memfs-server.

Checks that the /health endpoint returns 200 OK.
Exits 0 on success, 1 on failure (Docker healthcheck convention).
"""

import sys
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

HEALTH_URL = "http://localhost:8285/health"
TIMEOUT = 5  # seconds


def main():
    try:
        req = Request(HEALTH_URL, method="GET")
        with urlopen(req, timeout=TIMEOUT) as response:
            if response.status == 200:
                sys.exit(0)
            else:
                print(f"Health check failed: HTTP {response.status}", file=sys.stderr)
                sys.exit(1)
    except HTTPError as e:
        print(f"Health check failed: HTTP {e.code}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Health check failed: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Health check failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
