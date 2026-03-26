#!/usr/bin/env python3
"""
git-memfs-server.py — Docker-adapted git HTTP smart protocol server for Letta MemFS.

Designed to run as a sidecar container in a Docker Compose environment.
Receives proxied git smart-HTTP requests from Letta server and delegates to git http-backend.

Environment variables:
  MEMFS_PORT (default: 8285) — Port to listen on
  MEMFS_BASE_PATH (default: /data/memfs/repository) — Base path for bare repos
  MEMFS_DEFAULT_ORG (default: default-org) — Default org ID when header not present

Repo layout:
  {MEMFS_BASE_PATH}/{org_id}/{agent_id}/repo.git/

URL pattern from Letta's /v1/git/ proxy:
  /git/{agent_id}/state.git/info/refs
  /git/{agent_id}/state.git/git-upload-pack
  /git/{agent_id}/state.git/git-receive-pack
"""

import os
import subprocess
import sys
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = int(os.getenv("MEMFS_PORT", "8285"))
MEMFS_BASE = Path(os.getenv("MEMFS_BASE_PATH", "/data/memfs/repository"))
DEFAULT_ORG = os.getenv("MEMFS_DEFAULT_ORG", "default-org")


def log_json(level: str, message: str, **extra):
    """Structured JSON logging with timestamps."""
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "level": level,
        "message": message,
        **extra
    }
    print(json.dumps(entry), flush=True)


def find_or_create_repo(agent_id: str, org_id: str) -> Path:
    """Return path to the bare repo, creating it (and enabling http.receivepack) if needed."""
    repo = MEMFS_BASE / org_id / agent_id / "repo.git"
    if not repo.exists():
        # Fallback: scan all org dirs in case org_id header differs from stored value
        if MEMFS_BASE.exists():
            for org_dir in MEMFS_BASE.iterdir():
                candidate = org_dir / agent_id / "repo.git"
                if candidate.exists():
                    log_json("info", "Found repo under different org", 
                            agent_id=agent_id, stored_org=org_dir.name, header_org=org_id)
                    return candidate
        # Create fresh bare repo
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "http.receivepack", "true"],
            check=True, capture_output=True,
        )
        log_json("info", "Created new bare repo", agent_id=agent_id, org_id=org_id, path=str(repo))
    return repo


class GitHTTPHandler(BaseHTTPRequestHandler):

    def _read_body(self) -> bytes:
        """Read request body, handling both Content-Length and chunked encoding.

        The Letta proxy strips Content-Length and forwards with chunked
        transfer encoding, so we must handle both cases.
        """
        te = self.headers.get("Transfer-Encoding", "")
        if "chunked" in te.lower():
            body = b""
            while True:
                size_line = self.rfile.readline().strip()
                if not size_line:
                    break
                try:
                    chunk_size = int(size_line, 16)
                except ValueError:
                    break
                if chunk_size == 0:
                    self.rfile.readline()  # consume trailing \r\n
                    break
                body += self.rfile.read(chunk_size)
                self.rfile.readline()  # consume \r\n after chunk
            return body
        else:
            content_length = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(content_length) if content_length > 0 else b""

    def _parse_path(self):
        """
        Incoming path: /git/{agent_id}/state.git/{git_op_path}
        Returns: (agent_id, git_op_path, query_string)
        git_op_path is the part git http-backend needs when GIT_DIR is set directly,
        e.g. /info/refs or /git-upload-pack
        """
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        # Expected: ["git", agent_id, "state.git", ...]
        if len(parts) < 3 or parts[0] != "git":
            return None, None, None
        agent_id = parts[1]
        git_op = "/" + "/".join(parts[3:]) if len(parts) > 3 else "/"
        return agent_id, git_op, parsed.query if parsed.query else ""

    def _run_backend(self):
        agent_id, git_op, query = self._parse_path()
        if agent_id is None or git_op is None:
            self.send_error(400, "Invalid path — expected /git/{agent_id}/state.git/...")
            return

        org_id = self.headers.get("X-Organization-Id", DEFAULT_ORG)
        repo_path = find_or_create_repo(agent_id, org_id)

        body = self._read_body()

        # git http-backend needs:
        #   GIT_PROJECT_ROOT = parent dir of repo.git (forward slashes on Windows)
        #   PATH_INFO        = /repo.git/<op>  (must include the repo dirname)
        project_root = str(repo_path.parent).replace("\\", "/")
        full_path_info = "/repo.git" + git_op

        env = {
            **os.environ,
            "GIT_HTTP_EXPORT_ALL": "1",
            "GIT_PROJECT_ROOT": project_root,
            "PATH_INFO": full_path_info,
            "QUERY_STRING": query,
            "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "HTTP_GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
            "REMOTE_ADDR": self.client_address[0],
            "REMOTE_USER": "",
            "SERVER_NAME": "memfs-server",
            "SERVER_PORT": str(PORT),
            "SERVER_PROTOCOL": "HTTP/1.1",
        }

        result = subprocess.run(
            ["git", "http-backend"],
            input=body,
            capture_output=True,
            env=env,
        )

        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")
            log_json("error", "git http-backend failed", 
                    returncode=result.returncode, stderr=err, agent_id=agent_id)
            self.send_error(500, f"git http-backend exited {result.returncode}")
            return

        # Parse CGI-style response: headers block + \r\n\r\n + body
        raw = result.stdout
        sep = b"\r\n\r\n"
        split_pos = raw.find(sep)
        if split_pos == -1:
            sep = b"\n\n"
            split_pos = raw.find(sep)
        if split_pos == -1:
            self.send_error(502, "Malformed git http-backend response")
            return

        header_block = raw[:split_pos].decode(errors="replace")
        body_out = raw[split_pos + len(sep):]

        status = 200
        headers = []
        for line in header_block.splitlines():
            if not line.strip():
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip()
                if k.lower() == "status":
                    try:
                        status = int(v.split()[0])
                    except ValueError:
                        pass
                else:
                    headers.append((k, v))

        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)

    def do_GET(self):
        # Health check endpoint
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            response = json.dumps({"status": "ok"}).encode()
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        
        self._run_backend()

    def do_POST(self):
        self._run_backend()

    def log_message(self, format, *args):
        log_json("info", "HTTP request", 
                client=self.address_string(), 
                request=format % args)


if __name__ == "__main__":
    MEMFS_BASE.mkdir(parents=True, exist_ok=True)
    log_json("info", "Starting git-memfs-server", 
            host="0.0.0.0", port=PORT, memfs_base=str(MEMFS_BASE), default_org=DEFAULT_ORG)
    
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), GitHTTPHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        log_json("info", "Server stopped by user")
        sys.exit(0)
