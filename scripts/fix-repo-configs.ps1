# fix-repo-configs.ps1
# Fixes bare git repos under ~/.letta/memfs/repository so pushes are accepted.
# Run this after creating new agents if push fails with "refusing to update checked out branch".

$repoBase = "$env:USERPROFILE\.letta\memfs\repository"

$repos = Get-ChildItem $repoBase -Recurse -Filter "config" | Where-Object {
    $_.FullName -match "repo\.git"
}

foreach ($cfg in $repos) {
    $repoPath = $cfg.DirectoryName
    $cfgContent = Get-Content $cfg.FullName -Raw

    if ($cfgContent -match "bare = false" -and $cfgContent -notmatch "denyCurrentBranch") {
        git -C $repoPath config receive.denyCurrentBranch ignore
        Write-Host "Fixed: $repoPath"
    } elseif ($cfgContent -match "denyCurrentBranch") {
        Write-Host "Already fixed: $repoPath"
    } else {
        Write-Host "Skipped (bare=true or unknown): $repoPath"
    }
}

Write-Host "Done."