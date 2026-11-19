param(
    [string]$Distribution = '',
    [string]$Repository = '',
    [string]$Python = 'python3',
    [int]$Port = 8766,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
# Resolve through wslpath, including Windows drive paths and WSL UNC paths.
$wslArgs = @()
if ($Distribution) { $wslArgs += @('-d', $Distribution) }
if (-not $Repository) {
    $windowsRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
    $Repository = (& wsl.exe @wslArgs --exec wslpath -a -u $windowsRoot).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve repository. Pass -Repository with its Linux path.' }
}
$url = "http://127.0.0.1:$Port"
Write-Host "Starting Fusion from $Repository using $Python. Keep this window open."
Write-Host "Open $url after the server prints its listening address. Ctrl+C stops the server."
if (-not $NoBrowser) { Start-Process $url }
# Foreground server: no hidden process or log files in tracked artifact directories.
& wsl.exe @wslArgs --cd $Repository --exec env "OPENYIELD_REPOSITORY=$Repository" $Python -B -u workbench/fusion/server.py --port $Port
exit $LASTEXITCODE
