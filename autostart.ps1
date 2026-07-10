# Register/unregister the HTTP server to run at Windows logon (Startup folder, no admin needed).
# Uses pythonw.exe (no console window); server logs go to server.log (see http_server.py __main__).
# Usage:  .\autostart.ps1          -> register
#         .\autostart.ps1 -Remove  -> unregister
param([switch]$Remove)
$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup "chatgpt-web-controller.lnk"

if ($Remove) {
    if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force; Write-Output "AUTOSTART REMOVED: $lnkPath" }
    else { Write-Output "AUTOSTART NOT SET (nothing to remove)" }
    exit 0
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python).Source
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { throw "pythonw.exe not found next to $py" }

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $pyw
$lnk.Arguments = "`"$root\http_server.py`""
$lnk.WorkingDirectory = $root
$lnk.WindowStyle = 7   # minimized (pythonw is windowless anyway)
$lnk.Description = "chatgpt-web-controller HTTP server autostart"
$lnk.Save()
Write-Output "AUTOSTART REGISTERED: $lnkPath -> $pyw $root\http_server.py"
