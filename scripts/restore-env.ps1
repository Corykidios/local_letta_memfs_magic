# restore-env.ps1
# Restores all Windows user environment variables needed for Letta + MemFS.
# Run once after a fresh Windows setup or if env vars get wiped.
# UPDATE the node.exe path if nvm version changes.

Write-Host "Restoring Letta environment variables..."

[System.Environment]::SetEnvironmentVariable("LETTA_BASE_URL",         "http://localhost:8283",                                                                                          "User")
[System.Environment]::SetEnvironmentVariable("LETTA_MEMFS_LOCAL",      "1",                                                                                                              "User")
[System.Environment]::SetEnvironmentVariable("LETTA_MEMFS_SERVICE_URL","http://localhost:8285",                                                                                          "User")
[System.Environment]::SetEnvironmentVariable("LETTA_CODE_BIN",         "C:\Users\cccom\AppData\Roaming\nvm\v24.11.1\node.exe",                                                           "User")
[System.Environment]::SetEnvironmentVariable("LETTA_CODE_BIN_ARGS_JSON",'["C:\\Users\\cccom\\AppData\\Roaming\\npm\\node_modules\\@letta-ai\\letta-code\\letta.js"]',                   "User")

Write-Host "Done. Verify:"
foreach ($k in @("LETTA_BASE_URL","LETTA_MEMFS_LOCAL","LETTA_MEMFS_SERVICE_URL","LETTA_CODE_BIN","LETTA_CODE_BIN_ARGS_JSON")) {
    $v = [System.Environment]::GetEnvironmentVariable($k, "User")
    Write-Host "  $k = $v"
}
Write-Host ""
Write-Host "NOTE: These are also set in ~/.bashrc for Git Bash sessions."
Write-Host "Open a fresh terminal for changes to take effect."