# apply-patches.ps1
# Applies three Windows patches to @letta-ai/letta-code v0.19.6
# Run this after any npm update to letta-code, or after a fresh install.
# Fixes: ENAMETOOLONG on /reflect and new agent spawn on Windows.

param(
    [string]$NodeModules = "$env:APPDATA\npm\node_modules"
)

$f = "$NodeModules\@letta-ai\letta-code\letta.js"

if (-not (Test-Path $f)) {
    Write-Error "letta.js not found at: $f"
    exit 1
}

# Read as raw bytes to preserve encoding
$enc = New-Object System.Text.UTF8Encoding($false)
$bytes = [System.IO.File]::ReadAllBytes($f)
$content = $enc.GetString($bytes)

# Check version
$pkgPath = "$NodeModules\@letta-ai\letta-code\package.json"
$version = (Get-Content $pkgPath | ConvertFrom-Json).version
Write-Host "letta-code version: $version"
if ($version -ne "0.19.6") {
    Write-Warning "Version mismatch (expected 0.19.6, got $version). Patches may not apply cleanly. Proceeding anyway."
}

# Backup
$bak = "$f.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $f $bak -Force
Write-Host "Backup: $bak"

$patched = $false

# --- PATCH 1: Remove inline Authorization header from git calls ---
# Fixes: ENAMETOOLONG when git commands are run (clone, push, pull)
$old1 = 'const authArgs = token ? [
    "-c",
    `http.extraHeader=Authorization: Basic ${Buffer.from(`letta:${token}`).toString("base64")}`
  ] : [];'
$new1 = 'const authArgs = []; // patched: credential helper handles auth, inline extraHeader causes ENAMETOOLONG on Windows'
if ($content.Contains($old1)) {
    $content = $content.Replace($old1, $new1)
    Write-Host "PATCH 1 applied: removed inline Authorization header"
    $patched = $true
} elseif ($content.Contains("patched: credential helper handles auth")) {
    Write-Host "PATCH 1 already applied"
} else {
    Write-Warning "PATCH 1 FAILED: target string not found - may need manual review"
}

# --- PATCH 2: Disable .cmd shim creation on Windows ---
# Fixes: ENAMETOOLONG when subagents spawn via cmd.exe (8191 char limit)
# Requires LETTA_CODE_BIN env var to be set (see restore-env.ps1)
$win32BlockStart = $content.IndexOf("  if (process.platform === `"win32`") {", $content.IndexOf("function ensureLettaShimDir"))
if ($win32BlockStart -ge 0) {
    $returnShimDir = $content.IndexOf("return shimDir;", $win32BlockStart)
    if ($returnShimDir -ge 0) {
        $blockEnd = $returnShimDir + "return shimDir;".Length
        $oldBlock = $content.Substring($win32BlockStart, $blockEnd - $win32BlockStart)
        if (-not $oldBlock.Contains("patched")) {
            $newBlock = "  if (process.platform === `"win32`") { return null; /* patched: LETTA_CODE_BIN set, no .cmd shim needed - avoids ENAMETOOLONG */"
            $content = $content.Substring(0, $win32BlockStart) + $newBlock + $content.Substring($blockEnd)
            Write-Host "PATCH 2 applied: disabled .cmd shim on Windows"
            $patched = $true
        } else {
            Write-Host "PATCH 2 already applied"
        }
    }
} else {
    Write-Warning "PATCH 2 FAILED: ensureLettaShimDir win32 block not found"
}

# --- PATCH 3: Pass reflection prompt via stdin instead of -p arg ---
# Fixes: ENAMETOOLONG for /reflect (prompt too large for any command line)
$old3 = '  args.push("-p", userPrompt);'
$new3 = '  /* patched: prompt passed via stdin to avoid ENAMETOOLONG */'
if ($content.Contains($old3)) {
    $content = $content.Replace($old3, $new3)
    Write-Host "PATCH 3a applied: removed -p userPrompt arg"
    $patched = $true
} elseif ($content.Contains("patched: prompt passed via stdin")) {
    Write-Host "PATCH 3a already applied"
} else {
    Write-Warning "PATCH 3a FAILED: target string not found"
}

$old4 = 'const proc2 = spawn4(launcher.command, launcher.args, {
      cwd: process.cwd(),
      env: {
        ...process.env,
        ...inheritedApiKey && { LETTA_API_KEY: inheritedApiKey },
        ...inheritedBaseUrl && { LETTA_BASE_URL: inheritedBaseUrl },
        LETTA_CODE_AGENT_ROLE: "subagent",
        ...parentAgentId && { LETTA_PARENT_AGENT_ID: parentAgentId }
      }
    });'
$new4 = 'const proc2 = spawn4(launcher.command, launcher.args, {
      cwd: process.cwd(),
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        ...inheritedApiKey && { LETTA_API_KEY: inheritedApiKey },
        ...inheritedBaseUrl && { LETTA_BASE_URL: inheritedBaseUrl },
        LETTA_CODE_AGENT_ROLE: "subagent",
        ...parentAgentId && { LETTA_PARENT_AGENT_ID: parentAgentId }
      }
    });
    if (userPrompt && proc2.stdin) { proc2.stdin.write(userPrompt, "utf-8"); proc2.stdin.end(); } /* patched: prompt via stdin */'
if ($content.Contains($old4)) {
    $content = $content.Replace($old4, $new4)
    Write-Host "PATCH 3b applied: stdin pipe + prompt write"
    $patched = $true
} elseif ($content.Contains("patched: prompt via stdin")) {
    Write-Host "PATCH 3b already applied"
} else {
    Write-Warning "PATCH 3b FAILED: target string not found"
}

# Write out
if ($patched) {
    $outBytes = $enc.GetBytes($content)
    [System.IO.File]::WriteAllBytes($f, $outBytes)
    $verify = [System.IO.File]::ReadAllBytes($f) | Select-Object -First 4
    Write-Host "Written. First bytes: $($verify -join ' ') (should be: 35 33 47 117)"
}

Write-Host ""
Write-Host "Done. Restart Git Bash and run 'letta --memfs' to verify."