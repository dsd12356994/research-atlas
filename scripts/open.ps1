$ErrorActionPreference = 'Stop'
$atlasRoot = Split-Path -Parent $PSScriptRoot
$atlasUrl = 'http://127.0.0.1:8767'
try { $atlasHealth = Invoke-RestMethod "$atlasUrl/api/health" -TimeoutSec 2 } catch { $atlasHealth = $null }
if (-not $atlasHealth) {
    if (-not (Test-Path -LiteralPath (Join-Path $atlasRoot 'output/index.html'))) { throw 'Initialize the library first: python -m hub.setup' }
    Start-Process -FilePath (Join-Path $atlasRoot '.venv/Scripts/python.exe') -ArgumentList '-m','hub.server','--port','8767' -WorkingDirectory $atlasRoot -WindowStyle Hidden
    for ($atlasTry = 0; $atlasTry -lt 15; $atlasTry++) {
        Start-Sleep -Milliseconds 300
        try { $atlasHealth = Invoke-RestMethod "$atlasUrl/api/health" -TimeoutSec 1; break } catch { }
    }
}
if ($atlasHealth.app -eq 'research-atlas') { Start-Process $atlasUrl }
else { throw 'Port 8767 is unavailable. Start hub.server with a different --port.' }
