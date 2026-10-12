# Refresh the sources that block GitHub Actions' datacenter IPs (refresh: local
# in sources/sources.yaml) from this machine, then push the updated cache so the
# daily CI scrape carries those events forward. See docs/SOURCES.md.
#
# Run by hand:      powershell -ExecutionPolicy Bypass -File scripts\refresh_local.ps1
# Schedule daily:   see "Local refresh" in docs/DEPLOY.md
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull failed - resolve local changes first" }

$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$env:PYTHONIOENCODING = "utf-8"
Push-Location (Join-Path $repo "scraper")
& $py main.py refresh-local
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { throw "refresh-local failed ($rc)" }

git add scraper/output/source_cache.json
git diff --staged --quiet
if ($LASTEXITCODE -eq 0) { Write-Output "cache unchanged"; exit 0 }
git commit -m "data: local refresh of CI-blocked sources $(Get-Date -Format yyyy-MM-dd)"
for ($i = 1; $i -le 3; $i++) {
    git push
    if ($LASTEXITCODE -eq 0) { exit 0 }
    git pull --rebase --autostash
}
throw "push failed after retries"
