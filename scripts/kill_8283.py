import subprocess, re

r = subprocess.run("netstat -ano", capture_output=True, text=True, shell=True)
for line in r.stdout.splitlines():
    if ":8283 " in line and "LISTENING" in line:
        parts = line.split()
        pid = parts[-1]
        print(f"Killing PID {pid} on :8283")
        subprocess.run(f"taskkill /F /PID {pid}", shell=True)
