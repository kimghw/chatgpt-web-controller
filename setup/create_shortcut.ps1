# Create/repair the dedicated ChatGPT Chrome desktop shortcut ("ChatGPT <jeon-yong>.lnk").
# Reads cdp_port / chrome_path / user_data_dir from config.json (falls back to defaults).
# Korean filename is built from [char] codepoints to avoid console-encoding mangling.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)  # setup/ 의 상위 = 프로젝트 루트
$cfgPath = Join-Path $root "config.json"
$cfg = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }

$port = 9223; if ($cfg -and $cfg.cdp_port) { $port = $cfg.cdp_port }
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if ($cfg -and $cfg.chrome_path) { $chrome = $cfg.chrome_path }
$profileDir = "$env:USERPROFILE\.chrome-chatgpt-debug"
if ($cfg -and $cfg.user_data_dir) { $profileDir = [Environment]::ExpandEnvironmentVariables($cfg.user_data_dir) }

$desktop = [Environment]::GetFolderPath('Desktop')
$name = "ChatGPT " + [char]0xC804 + [char]0xC6A9   # "ChatGPT 전용"
$final = Join-Path $desktop "$name.lnk"
$tmp = Join-Path $desktop "ChatGPT-Auto.lnk"       # COM writes ASCII name reliably; rename after

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($tmp)
$lnk.TargetPath = $chrome
$lnk.Arguments = "--remote-debugging-port=$port --user-data-dir=`"$profileDir`" --no-first-run --no-default-browser-check https://chatgpt.com/"
$lnk.WorkingDirectory = Split-Path -Parent $chrome
$lnk.IconLocation = "$chrome,0"
$lnk.Description = "ChatGPT automation Chrome (CDP $port, dedicated profile)"
$lnk.Save()

if (Test-Path $final) { Remove-Item $final -Force }
Rename-Item $tmp -NewName "$name.lnk"
Write-Output "SHORTCUT OK: $final (port=$port, profile=$profileDir)"
