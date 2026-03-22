# Letta Local MemFS — Personal Startup Guide

## The One-Sentence Version
You need THREE things running before `letta --memfs` works:
Redis, the memfs git server, and the Letta server — in that order.

---

## Every Time You Want To Use Letta + MemFS

### Step 1 — Redis (if not already running)
Open PowerShell and run:
```powershell
C:\c\apps\pkm\redis-windows\redis-server.exe --protected-mode no
```
Leave that window open. You'll see the Redis logo. That means it's fine.

### Step 2 — The MemFS Git Server
Open another PowerShell (or terminal) and run:
```powershell
python C:\c\apps\servers\git-memfs-server.py
```
You'll see:
```
[git-memfs] Starting on http://127.0.0.1:8285
[git-memfs] Repo root: C:\Users\cccom\.letta\memfs\repository
```
Leave that window open. It logs every git clone/push/pull hit.

### Step 3 — Letta Server
Open another terminal and run:
```powershell
cd C:\c\apps\clients\letta
uv run letta server
```
You'll see `MemfsClient initialized` near the top of the output
and eventually `Uvicorn running on http://localhost:8283`.
Leave that window open.

### Step 4 — Letta Code (Git Bash only!)
Open **Git Bash** (not PowerShell, not CMD) and run:
```bash
letta --memfs
```
The `--memfs` flag is only needed the FIRST time for each agent.
After that, just use `letta`.

---

## If Something Breaks

**"--memfs is only available on Letta Cloud"**
→ You're not in Git Bash, OR you opened Git Bash before today's setup.
  Close it and open a fresh Git Bash window.

**"501 Not Implemented" during clone**
→ The Letta server started before conf.yaml existed, or before
  the memfs server was running. Kill letta server, restart it.

**"expected flush after ref listing"**
→ The memfs git server isn't running. Check Step 2.

**"EPERM: operation not permitted, mkdir ...Git\.letta"**
→ You ran `letta` from the wrong directory. Always use Git Bash
  and it will auto-cd to ~ on open (this is already configured).

**Port 8283 already in use**
→ Old Letta server still running. Run:
  `python C:\c\apps\servers\kill_8283.py`

**Port 8285 already in use**
→ Old memfs server still running. Run:
  `python C:\c\apps\servers\kill_8285.py`

---

## File Map — What Lives Where

| File | Purpose |
|------|---------|
| `C:\c\apps\servers\git-memfs-server.py` | The memfs git sidecar server |
| `C:\c\apps\servers\start-letta.bat` | (Optional) starts memfs + letta together |
| `C:\c\apps\servers\kill_8283.py` | Kills stuck Letta server |
| `C:\c\apps\servers\kill_8285.py` | Kills stuck memfs server |
| `C:\Users\cccom\.letta\conf.yaml` | Tells Letta where the memfs server is |
| `C:\Users\cccom\.bashrc` | Has `LETTA_MEMFS_LOCAL=1` export |
| `C:\Users\cccom\.bash_profile` | Sources .bashrc so Git Bash sees it |
| `C:\Users\cccom\.letta\memfs\` | Where git repos for each agent live |

---

## What MemFS Actually Gives You
- Every memory block change your agent makes is **git-committed** locally
- Memory lives at `~/.letta/memfs/repository/{org}/{agent-id}/repo.git/`
- You get full version history — you can `git log` an agent's memory
- Letta Code's `/memory` commands and `/reflect` all work normally
