#!/usr/bin/env python3
"""
git-memfs-server.py — Minimal local git HTTP smart protocol server for Letta MemFS.

Bridges the gap between Letta's self-hosted OSS server and MemFS by serving
bare git repos locally via git http-backend, so Letta Code can clone/push/pull
against your own machine instead of Letta Cloud.

Repo layout (mirrors LocalStorageBackend):
  ~/.letta/memfs/repository/{org_id}/{agent_id}/repo.git/

URL pattern from Letta's /v1/git/ proxy:
  /git/{agent_id}/state.git/info/refs
  /git/{agent_id}/state.git/git-upload-pack
  /git/{agent_id}/state.git/git-receive-pack

Run:
  python git-memfs-server.py

Then set in ~/.letta/conf.yaml:
  LETTA_MEMFS_SERVICE_URL: "http://localhost:8285"
"""

import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = 8285
MEMFS_BASE = Path.home() / ".letta" / "memfs" / "repository"
DEFAULT_ORG = "default-org"


def find_or_create_repo(agent_id: str, org_id: str) -> Path:
    """Return path to the bare repo, creating it (and enabling http.receivepack) if needed."""
    repo = MEMFS_BASE / org_id / agent_id / "repo.git"
    if not repo.exists():
        # Fallback: scan all org dirs in case org_id header differs from stored value
        if MEMFS_BASE.exists():
            for org_dir in MEMFS_BASE.iterdir():
                candidate = org_dir / agent_id / "repo.git"
                if candidate.exists():
                    print(f"[git-memfs] Found repo under org {org_dir.name} (header said {org_id})", flush=True)
                    return candidate
        # Create fresh bare repo
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "http.receivepack", "true"],
            check=True, capture_output=True,
        )
        print(f"[git-memfs] Created bare repo at {repo}", flush=True)
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
        return agent_id, git_op, parsed.query or ""

    def _run_backend(self):
        agent_id, git_op, query = self._parse_path()
        if agent_id is None:
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
            "REMOTE_ADDR": "127.0.0.1",
            "REMOTE_USER": "",
            "SERVER_NAME": "localhost",
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
            print(f"[git-memfs] git http-backend error: {err}", flush=True)
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
        self._run_backend()

    def do_POST(self):
        self._run_backend()

    def log_message(self, fmt, *args):
        print(f"[git-memfs] {self.address_string()} — {fmt % args}", flush=True)


if __name__ == "__main__":
    MEMFS_BASE.mkdir(parents=True, exist_ok=True)
    print(f"[git-memfs] Starting on http://127.0.0.1:{PORT}", flush=True)
    print(f"[git-memfs] Repo root: {MEMFS_BASE}", flush=True)
    print(f"[git-memfs] Make sure ~/.letta/conf.yaml contains:", flush=True)
    print(f"[git-memfs]   LETTA_MEMFS_SERVICE_URL: http://localhost:{PORT}", flush=True)
    try:
        HTTPServer(("127.0.0.1", PORT), GitHTTPHandler).serve_forever()
    except KeyboardInterrupt:
        print("\n[git-memfs] Stopped.", flush=True)
