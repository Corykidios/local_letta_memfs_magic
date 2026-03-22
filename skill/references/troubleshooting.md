# Troubleshooting Reference

## Error Table

### "only available on Letta Cloud (api.letta.com)"

**Cause:** `LETTA_MEMFS_LOCAL` env var is not set in the current shell.

**Fix:**
1. Check: `echo $LETTA_MEMFS_LOCAL` — should print `1`
2. If blank: open a **fresh** Git Bash window (not PowerShell, not CMD)
3. If still blank after fresh window: check `~/.bash_profile` sources `~/.bashrc`
4. Check `~/.bashrc` contains `export LETTA_MEMFS_LOCAL=1`

Note: the check in letta.js is `=== "1"` — must be the digit 1, not "true".

---

### "501 Not Implemented" during git clone

**Cause A:** Letta server started before `conf.yaml` existed.
**Fix:** Restart the Letta server after creating `conf.yaml`.

**Cause B:** `conf.yaml` has wrong format.
**Fix:** Must be nested:
```yaml
letta:
  memfs_service_url: "http://localhost:8285"
```
Bare `LETTA_MEMFS_SERVICE_URL: "..."` at the top level is silently ignored.

**Verify:**
```bash
cd /path/to/letta
uv run python -c "from letta.settings import settings; print(settings.memfs_service_url)"
# Must print: http://localhost:8285
# If None: conf.yaml isn't loading
```

---

### "expected flush after ref listing"

**Cause:** `git-memfs-server.py` is not running, OR running an old version
that doesn't handle chunked transfer encoding.

Letta's httpx proxy strips `Content-Length` from forwarded requests and
re-encodes the body as chunked transfer encoding. Old versions of the sidecar
read 0 bytes from the body, passed empty stdin to `git http-backend`, and got
a malformed response back.

**Fix:** Ensure the sidecar has the `_read_body()` method that handles both
`Content-Length` and `Transfer-Encoding: chunked`. Restart the sidecar.

---

### "TypeError: GitOperations.__init__() got an unexpected keyword argument 'redis_client'"

**Cause:** Source patch not applied.

**Fix:** In `letta/services/memory_repo/memfs_client_base.py`, line ~54:
```python
# Before:
self.git = GitOperations(storage=self.storage, redis_client=None)
# After:
self.git = GitOperations(storage=self.storage)
```

---

### "EPERM: operation not permitted, mkdir ...Git\.letta"

**Cause:** Running `letta` from `/` in Git Bash, which maps to
`C:\Program Files\Git\` on Windows. Letta Code tries to create a `.letta`
directory in CWD and doesn't have permission to write there.

**Fix:** Always `cd ~` before running `letta`. Or add to `~/.bash_profile`:
```bash
cd ~
```

---

### "Address already in use" / Port 8283 or 8285 taken

**Cause:** Old server instance still running (e.g. after a crash).

**Fix:**
```python
# kill_8283.py / kill_8285.py (in scripts/)
import subprocess
r = subprocess.run("netstat -ano", capture_output=True, text=True, shell=True)
for line in r.stdout.splitlines():
    if ":8283 " in line and "LISTENING" in line:   # change port as needed
        pid = line.split()[-1]
        subprocess.run(f"taskkill /F /PID {pid}", shell=True)
```

---

### Memfs server shows no hits during clone

**Cause:** Letta server didn't pick up `LETTA_MEMFS_SERVICE_URL`.

**Diagnosis:** Check the Letta server startup log for:
```
MemfsClient initialized with local storage at ~/.letta/memfs
```
If this line is absent, the setting wasn't loaded. Restart the Letta server.

---

### "git-memory-enabled" tag added but memory blocks still used

**Cause:** This is expected behaviour on the first enable. Letta Code
detaches the standard memory tools and swaps the system prompt only for
that session. On subsequent launches, the tag is detected and memfs is
restored automatically.

If blocks persist after multiple sessions, check that `GitEnabledBlockManager`
is active: search the Letta server log for `GitEnabledBlockManager`.

---

## Verification Checklist

Run through this in order when something is broken:

```bash
# 1. Is LETTA_MEMFS_LOCAL set?
echo $LETTA_MEMFS_LOCAL        # expect: 1

# 2. Is the sidecar running?
curl http://localhost:8285/git/test/state.git/info/refs?service=git-upload-pack \
  -H "X-Organization-Id: test"
# expect: HTTP 200 with Content-Type: application/x-git-upload-pack-advertisement

# 3. Does Letta server see the sidecar URL?
# (run from letta install dir)
uv run python -c "from letta.settings import settings; print(settings.memfs_service_url)"
# expect: http://localhost:8285

# 4. Does the full proxy chain work?
python scripts/check_settings.py
# expect: STATUS 200

# 5. Is Redis running?
redis-cli ping    # expect: PONG
```
