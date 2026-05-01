# Deploy MQL5 EAs to MT5 terminals by copying source and compiling in-place.
# For each strategy, for each target terminal:
#   1. Copy .mq5 source into MQL5/Experts/<EAName>/
#   2. Run that terminal own metaeditor64.exe to compile in-place
#   3. Verify .ex5 was produced
# Param Strategies: JSON array of strategy names. Example: ["zone_signal","hedge_lock"]
param(
    [Parameter(Mandatory)]
    [string]$Strategies
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Config = Get-Content "$RepoRoot\deploy.json" -Raw | ConvertFrom-Json

$strategyList = $Strategies | ConvertFrom-Json
$failed = @()
$terminalsToRestart = @{}

foreach ($name in $strategyList) {
    $strat = $Config.strategies.$name
    if (-not $strat) {
        Write-Warning "[$name] Not found in deploy.json - skipping"
        continue
    }

    $sourceFile = Join-Path $RepoRoot $strat.ea_source
    if (-not (Test-Path $sourceFile)) {
        Write-Warning "[$name] Source not found: $sourceFile - skipping"
        continue
    }

    $eaBaseName = [System.IO.Path]::GetFileNameWithoutExtension($sourceFile)

    foreach ($termName in $strat.deploy_to) {
        $terminal = $Config.terminals.$termName
        if (-not $terminal) {
            Write-Warning "[$name] Terminal '$termName' not found in config - skipping"
            continue
        }

        # Resolve AppData against the user that actually runs THIS MT5 terminal,
        # NOT the GitHub Actions runner's user (Administrator). Each MT5 instance
        # may run under a different Windows account (QuangXAU, luanxau, ...).
        # Fall back to $env:APPDATA only if user_profile is missing in deploy.json.
        if ($terminal.user_profile) {
            $userAppData = "C:\Users\$($terminal.user_profile)\AppData\Roaming"
        } else {
            $userAppData = $env:APPDATA
            Write-Warning "[$name -> $termName] No user_profile set in deploy.json, falling back to runner's APPDATA: $userAppData"
        }
        $terminalDataRoot = "$userAppData\MetaQuotes\Terminal\$($terminal.hash)"

        $expertsRoot = "$terminalDataRoot\MQL5\Experts"
        $expertsDir = Join-Path $expertsRoot $eaBaseName

        if (-not (Test-Path $expertsDir)) {
            New-Item -ItemType Directory -Path $expertsDir -Force | Out-Null
            Write-Host "[$name -> $termName] Created subfolder: $expertsDir"
        }

        # Remove legacy .ex5 / .mq5 at Experts root (pre-subfolder layout)
        foreach ($ext in @(".ex5", ".mq5")) {
            $legacyFile = Join-Path $expertsRoot "$eaBaseName$ext"
            if (Test-Path $legacyFile) {
                Remove-Item $legacyFile -Force
                Write-Host "[$name -> $termName] Removed legacy file at root: $legacyFile"
            }
        }

        $destMq5 = Join-Path $expertsDir "$eaBaseName.mq5"
        $destEx5 = Join-Path $expertsDir "$eaBaseName.ex5"
        $logFile = Join-Path $expertsDir "$eaBaseName.log"

        # Backup existing .ex5
        if (Test-Path $destEx5) {
            $backupFile = "$destEx5.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Copy-Item $destEx5 $backupFile
            Write-Host "[$name -> $termName] Backup: $backupFile"
        }

        # Copy source .mq5 into the terminal's Experts subfolder
        Copy-Item $sourceFile $destMq5 -Force
        Write-Host "[$name -> $termName] Source copied to $destMq5"

        # Compile in-place using THIS terminal's MetaEditor + include dir
        $MetaEditor = "$($terminal.mt5_install_dir)\metaeditor64.exe"
        $includeDir = "$terminalDataRoot\MQL5"

        if (-not (Test-Path $MetaEditor)) {
            Write-Error "[$name -> $termName] MetaEditor not found: $MetaEditor"
            $failed += "$name@$termName"
            continue
        }

        Write-Host "[$name -> $termName] Compiling in-place: $destMq5"
        Write-Host "[$name -> $termName] MetaEditor: $MetaEditor"

        $compileArgs = "/compile:`"$destMq5`" /include:`"$includeDir`" /log:`"$logFile`""
        Start-Process -FilePath $MetaEditor -ArgumentList $compileArgs `
            -Wait -PassThru -NoNewWindow | Out-Null

        if (Test-Path $logFile) {
            $logContent = Get-Content $logFile -Raw -Encoding Unicode
            Write-Host $logContent

            if ($logContent -match "(\d+) error\(s\)") {
                $errorCount = [int]$Matches[1]
                if ($errorCount -gt 0) {
                    Write-Error "[$name -> $termName] Compilation FAILED with $errorCount error(s)"
                    $failed += "$name@$termName"
                    continue
                }
            }
        }

        if (Test-Path $destEx5) {
            $size = (Get-Item $destEx5).Length
            Write-Host "[$name -> $termName] OK Compiled & deployed: $destEx5 ($size bytes)"
            $terminalsToRestart[$termName] = $terminal.mt5_install_dir
        } else {
            Write-Error "[$name -> $termName] .ex5 not found after compilation"
            $failed += "$name@$termName"
            continue
        }

        # Setup data symlink if agent is configured
        if ($strat.agent -and $strat.agent.data_subfolder) {
            $dataSubfolder = $strat.agent.data_subfolder
            $filesDir = "$terminalDataRoot\MQL5\Files\$dataSubfolder"
            $agentDataDir = Join-Path $RepoRoot "strategies\$name\data"

            if (-not (Test-Path $agentDataDir)) {
                New-Item -ItemType Directory -Path $agentDataDir -Force | Out-Null
            }

            # Create junction: MT5 Files/{EAName} -> strategies/{name}/data.
            # If $filesDir already exists as a real directory (not a junction), convert it:
            # back up any files into the agent data dir, remove the real dir, then create the junction.
            # Without this, agent writes never reach MT5 because the EA reads a different folder.
            $existing = Get-Item $filesDir -Force -ErrorAction SilentlyContinue
            $isLink = $existing -and ($existing.LinkType -in 'Junction','SymbolicLink')

            if ($existing -and -not $isLink) {
                Write-Host "[$name -> $termName] Found real dir at $filesDir - converting to junction"
                Get-ChildItem -LiteralPath $filesDir -Force -ErrorAction SilentlyContinue | ForEach-Object {
                    $dest = Join-Path $agentDataDir $_.Name
                    if (-not (Test-Path -LiteralPath $dest)) {
                        Move-Item -LiteralPath $_.FullName -Destination $dest -Force
                        Write-Host "[$name -> $termName]   migrated $($_.Name) -> data\"
                    } else {
                        # Collision: prefer the newer file by LastWriteTime so we don't
                        # silently lose a live signal that MT5/agent just wrote.
                        $srcTime = $_.LastWriteTimeUtc
                        $dstTime = (Get-Item -LiteralPath $dest -Force).LastWriteTimeUtc
                        if ($srcTime -gt $dstTime) {
                            $stash = Join-Path $env:TEMP "kog-stale-$name-$($_.Name).$(Get-Date -Format yyyyMMddHHmmss)"
                            Move-Item -LiteralPath $dest -Destination $stash -Force
                            Move-Item -LiteralPath $_.FullName -Destination $dest -Force
                            Write-Host "[$name -> $termName]   replaced stale repo data\$($_.Name) (src newer); old -> $stash"
                        } else {
                            $stash = Join-Path $env:TEMP "kog-stale-$name-$($_.Name).$(Get-Date -Format yyyyMMddHHmmss)"
                            Move-Item -LiteralPath $_.FullName -Destination $stash -Force
                            Write-Host "[$name -> $termName]   kept repo data\$($_.Name) (newer); MT5 copy -> $stash"
                        }
                    }
                }
                Remove-Item -LiteralPath $filesDir -Force -Recurse
                $existing = $null
            }

            if (-not $existing) {
                cmd /c mklink /J "$filesDir" "$agentDataDir" | Out-Null
                Write-Host "[$name -> $termName] Junction: $filesDir -> $agentDataDir"
            } else {
                Write-Host "[$name -> $termName] Junction already in place: $filesDir -> $($existing.Target)"
            }
        }
    }
}

# Restart each terminal that had a successful compile, so MT5 picks up the new .ex5.
# MT5 does NOT reload .ex5 from disk while the EA is attached - terminal restart is required.
foreach ($termName in $terminalsToRestart.Keys) {
    $installDir = $terminalsToRestart[$termName]
    $exePath = Join-Path $installDir "terminal64.exe"

    if (-not (Test-Path $exePath)) {
        Write-Warning "[$termName] terminal64.exe not found at $exePath - skip restart"
        continue
    }

    $procs = Get-Process -Name terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exePath }

    if ($procs) {
        Write-Host "[$termName] Stopping $($procs.Count) terminal64.exe process(es) at $exePath"
        foreach ($p in $procs) {
            $p.CloseMainWindow() | Out-Null
            if (-not $p.WaitForExit(8000)) {
                Write-Warning "[$termName] Graceful close timed out - killing PID $($p.Id)"
                $p.Kill()
                $p.WaitForExit(5000) | Out-Null
            }
        }
        Start-Sleep -Seconds 2
    } else {
        Write-Host "[$termName] No running terminal64.exe at $exePath (nothing to stop)"
    }

    Write-Host "[$termName] Launching $exePath (detached via WMI)"
    # Use WMI Win32_Process.Create so the process is parented to wmiprvse and
    # survives the GitHub Actions job object cleanup (which kills child PIDs).
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = "`"$exePath`""
        CurrentDirectory = $installDir
    }
    if ($result.ReturnValue -ne 0) {
        Write-Error "[$termName] WMI Create failed with return $($result.ReturnValue)"
    } else {
        Write-Host "[$termName] Started PID $($result.ProcessId)"
    }
}

if ($failed.Count -gt 0) {
    Write-Error "FAILED: $($failed -join ', ')"
    exit 1
}

Write-Host "All deployments complete"
