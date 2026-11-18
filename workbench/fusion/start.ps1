param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$base = $PSScriptRoot
$url = 'http://127.0.0.1:8766'
try { $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2 } catch { $health = $null }
if ($health -and $health.app -eq 'openyield-fusion') {
    Write-Host "OpenYield Fusion is ready: $url"
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
}
if ($health) { throw 'Port 8766 is occupied by a different service.' }
$repo = '/home/laiqirui/openyield-web-312f137'
$python = '/home/laiqirui/miniconda3/envs/openyield/bin/python'
$deps = '/home/laiqirui/openyield-weight-audit-20261116/dev/audit_deps'
$linuxBase = '/mnt/' + $base.Substring(0,1).ToLower() + $base.Substring(2).Replace('\','/')
$arguments = @('-d', 'Ubuntu', '--cd', $repo, '--', 'env', "PYTHONPATH=$deps", "OPENYIELD_REPOSITORY=$repo", $python, '-B', '-u', ('"' + $linuxBase + '/server.py"'), '--port', '8766')
$process = Start-Process -FilePath 'wsl.exe' -ArgumentList $arguments -WorkingDirectory $base -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $base 'artifacts/server.stdout.log') -RedirectStandardError (Join-Path $base 'artifacts/server.stderr.log')
for ($attempt = 0; $attempt -lt 100; $attempt++) {
    try { $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 1 } catch { $health = $null }
    if ($health -and $health.app -eq 'openyield-fusion') {
        Write-Host "OpenYield Fusion is ready: $url"
        if (-not $NoBrowser) { Start-Process $url }
        exit 0
    }
    $process.Refresh()
    if ($process.HasExited) { throw 'The service stopped. See artifacts/server.stderr.log.' }
    Start-Sleep -Milliseconds 250
}
throw 'The service is not responding. See artifacts/server.stderr.log.'
