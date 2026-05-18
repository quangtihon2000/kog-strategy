<#
.SYNOPSIS
  List MT5 instances on this Windows VPS for a given user profile.

.DESCRIPTION
  Scans `C:\Program Files\*\terminal64.exe` for installs, then maps each
  Terminal data-folder hash under `<user>\AppData\Roaming\MetaQuotes\Terminal\`
  back to its install dir via `origin.txt`. Also reports running terminal64.exe
  processes owned by the user and the most recent login line from each
  terminal's logs.

  Writes a plain-text report to -OutputPath (default:
  `mt5-instances-<USER>-<yyyyMMdd-HHmmss>.txt` in the current directory) and
  also prints to console.

.PARAMETER User
  Windows user whose MetaQuotes profile to scan. Default: QuangXAU.

.PARAMETER OutputPath
  Where to save the report. Default: auto-named .txt in CWD.

.EXAMPLE
  .\list-mt5-instances.ps1
  .\list-mt5-instances.ps1 -User luanxau -OutputPath C:\Temp\mt5.txt
#>

param(
    [string]$User = 'QuangXAU',
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

if (-not $OutputPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutputPath = Join-Path (Get-Location) "mt5-instances-$User-$stamp.txt"
}

$base = "C:\Users\$User\AppData\Roaming\MetaQuotes\Terminal"
if (-not (Test-Path $base)) {
    Write-Error "MetaQuotes profile not found: $base"
    exit 1
}

$report = & {
    "MT5 instance report for user: $User"
    "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
    "Host: $env:COMPUTERNAME"
    ""

    "=== 1. MT5 installs in C:\Program Files ==="
    $installs = Get-ChildItem -Path 'C:\Program Files' -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'terminal64.exe') } |
        Select-Object Name, FullName,
            @{N='FileVersion';E={(Get-Item (Join-Path $_.FullName 'terminal64.exe')).VersionInfo.FileVersion}}
    ($installs | Format-Table -AutoSize | Out-String).TrimEnd()
    ""

    "=== 2. Terminal data folders for $User (hash -> install) ==="
    $terminals = Get-ChildItem $base -Directory |
        Where-Object { $_.Name -match '^[A-F0-9]{32}$' } |
        ForEach-Object {
            $origin = Join-Path $_.FullName 'origin.txt'
            [PSCustomObject]@{
                Hash       = $_.Name
                InstallDir = if (Test-Path $origin) { (Get-Content $origin -Raw).Trim() } else { '(no origin.txt)' }
                LastWrite  = $_.LastWriteTime
            }
        }
    ($terminals | Sort-Object LastWrite -Descending | Format-Table -AutoSize | Out-String).TrimEnd()
    ""

    "=== 3. Running terminal64.exe processes owned by $User ==="
    $running = Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'" |
        ForEach-Object {
            $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
            [PSCustomObject]@{
                PID     = $_.ProcessId
                Owner   = if ($owner) { "$($owner.Domain)\$($owner.User)" } else { '(unknown)' }
                ExePath = $_.ExecutablePath
                Started = $_.CreationDate
            }
        } | Where-Object { $_.Owner -like "*\$User" }
    if ($running) {
        ($running | Format-Table -AutoSize | Out-String).TrimEnd()
    } else {
        "  (none running)"
    }
    ""

    "=== 4. Latest login per terminal (parsed from logs) ==="
    $logins = foreach ($t in $terminals) {
        $logDir = Join-Path $base "$($t.Hash)\logs"
        if (-not (Test-Path $logDir)) { continue }
        $latest = Get-ChildItem $logDir -Filter '*.log' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $latest) { continue }
        $line = Select-String -Path $latest.FullName -Pattern 'login: \d+' |
            Select-Object -Last 1
        [PSCustomObject]@{
            Hash      = $t.Hash
            LatestLog = $latest.Name
            LoginLine = if ($line) { $line.Line.Trim() } else { '(no login line found)' }
        }
    }
    ($logins | Format-Table -AutoSize -Wrap | Out-String).TrimEnd()
}

# Print to console
$report | ForEach-Object { Write-Output $_ }

# Save to file as UTF-8 (PS 5.1-compatible — Tee-Object has no -Encoding here)
$report | Out-File -FilePath $OutputPath -Encoding UTF8

Write-Host ""
Write-Host "Report saved: $OutputPath" -ForegroundColor Green
