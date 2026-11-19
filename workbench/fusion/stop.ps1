param([int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$url = "http://127.0.0.1:$Port"
try { $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2 } catch { Write-Host 'OpenYield Fusion is not running.'; exit 0 }
if ($health.app -ne 'openyield-fusion') { throw 'Port 8766 does not belong to OpenYield Fusion.' }
$context = Invoke-RestMethod -Uri "$url/api/context" -TimeoutSec 5
$headers = @{ 'Origin' = $url; 'X-OpenYield-Token' = $context.token }
Invoke-RestMethod -Uri "$url/api/shutdown" -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' | Out-Null
Write-Host 'OpenYield Fusion is stopping. Current work is cancelled and written results are preserved.'
