<#
.SYNOPSIS
  Nitro Console "console mode": TV-only display, no sleep on AC, Steam Big Picture. Fully reversible.
.DESCRIPTION
  on     - save a snapshot, then: display -> external (TV), AC sleep/monitor timeouts -> never,
           open Steam Big Picture.
  off    - restore everything from the snapshot, close Big Picture, delete the snapshot.
  status - print current state (on if a snapshot exists).
  -DryRun prints the plan and changes NOTHING (no snapshot written).
  Audio to HDMI is NOT switched (needs a third-party module); Windows usually switches it itself.
  The last output line is JSON for the server: {"action","dry_run","ok","state","steps","message"}.
.EXAMPLE
  .\scripts\console-mode.ps1 -Action on -DryRun
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('on', 'off', 'status')][string]$Action,
    [switch]$DryRun,
    [switch]$Force,
    [string]$SnapshotPath = ''
)
$ErrorActionPreference = 'Stop'
# PS 5.1: $PSScriptRoot is empty inside param() defaults, so resolve here.
if (-not $SnapshotPath) {
    $SnapshotPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\data\console-snapshot.json'
}
$steps = New-Object System.Collections.Generic.List[string]

function Out-Result([bool]$ok, [string]$state, [string]$message) {
    $obj = [ordered]@{ action = $Action; dry_run = [bool]$DryRun; ok = $ok; state = $state
                       steps = $steps.ToArray(); message = $message }
    Write-Output ($obj | ConvertTo-Json -Compress -Depth 3)
    if (-not $ok) { exit 1 }
    exit 0
}

function Step([string]$text, [scriptblock]$do) {
    if ($DryRun) { $steps.Add("[dry-run] $text"); return }
    & $do
    $steps.Add($text)
}

# powercfg output is localized; the last two hex values are the current AC and DC indexes.
function Get-PowerAc([string]$sub, [string]$setting) {
    $out = (& powercfg /query SCHEME_CURRENT $sub $setting) -join "`n"
    $hex = [regex]::Matches($out, '0x[0-9a-fA-F]{8}') | ForEach-Object { $_.Value }
    if ($hex.Count -lt 2) { throw "cannot read powercfg $sub $setting" }
    return [Convert]::ToInt32($hex[$hex.Count - 2], 16)
}

function Set-PowerAc([string]$sub, [string]$setting, [int]$value) {
    & powercfg /setacvalueindex SCHEME_CURRENT $sub $setting $value
    if ($LASTEXITCODE -ne 0) { throw "powercfg /setacvalueindex $sub $setting failed" }
}

function Get-ScreenCount {
    Add-Type -AssemblyName System.Windows.Forms
    return [System.Windows.Forms.Screen]::AllScreens.Count
}

function Get-ActiveMonitorCount {
    $m = Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorBasicDisplayParams -ErrorAction SilentlyContinue
    return @($m | Where-Object { $_.Active }).Count
}

# Topology to restore. Windows remembers the layout (positions, primary) per topology itself,
# so restoring the topology also restores the user's arrangement.
#   desktops >= 2               -> extend
#   1 desktop, >= 2 monitors on -> clone (duplicate)
#   otherwise                   -> internal
function Get-Topology([int]$screens, [int]$monitors) {
    if ($screens -ge 2) { return 'extend' }
    if ($monitors -ge 2) { return 'clone' }
    return 'internal'
}

function Set-ActiveScheme {
    & powercfg /setactive SCHEME_CURRENT
    if ($LASTEXITCODE -ne 0) { throw 'powercfg /setactive failed' }
}

$snapshotExists = Test-Path $SnapshotPath

try {
    switch ($Action) {
        'status' {
            Out-Result $true ($(if ($snapshotExists) { 'on' } else { 'off' })) ''
        }
        'on' {
            if ($snapshotExists) { Out-Result $true 'on' 'already on' }
            $screens = Get-ScreenCount
            $monitors = Get-ActiveMonitorCount
            if ([Math]::Max($screens, $monitors) -lt 2 -and -not $Force) {
                Out-Result $false 'off' 'TV not detected (only one display). Connect HDMI or use -Force.'
            }
            $topology = Get-Topology $screens $monitors
            $snapshot = [ordered]@{
                created        = (Get-Date).ToString('o')
                topology       = $topology
                standby_ac_s   = Get-PowerAc 'SUB_SLEEP' 'STANDBYIDLE'
                monitor_ac_s   = Get-PowerAc 'SUB_VIDEO' 'VIDEOIDLE'
            }
            Step "save snapshot to $SnapshotPath (topology=$topology, standby_ac=$($snapshot.standby_ac_s)s, monitor_ac=$($snapshot.monitor_ac_s)s)" {
                New-Item -ItemType Directory -Force (Split-Path $SnapshotPath) | Out-Null
                $snapshot | ConvertTo-Json | Set-Content -Encoding ascii $SnapshotPath
            }
            Step 'display: TV only (DisplaySwitch /external)' { & DisplaySwitch.exe /external }
            Step 'power (AC): never sleep, never turn off display' {
                Set-PowerAc 'SUB_SLEEP' 'STANDBYIDLE' 0
                Set-PowerAc 'SUB_VIDEO' 'VIDEOIDLE' 0
                Set-ActiveScheme
            }
            Step 'open Steam Big Picture' { Start-Process 'steam://open/bigpicture' }
            Out-Result $true ($(if ($DryRun) { 'off' } else { 'on' })) ''
        }
        'off' {
            if (-not $snapshotExists) { Out-Result $true 'off' 'already off (no snapshot)' }
            $snap = Get-Content -Raw $SnapshotPath | ConvertFrom-Json
            $topology = [string]$snap.topology
            if ($topology -notin @('extend', 'clone', 'internal')) { throw "bad topology in snapshot: $topology" }
            # Power first: if it fails we throw and the snapshot stays for a retry.
            Step "power (AC): restore standby=$($snap.standby_ac_s)s, monitor=$($snap.monitor_ac_s)s" {
                Set-PowerAc 'SUB_SLEEP' 'STANDBYIDLE' ([int]$snap.standby_ac_s)
                Set-PowerAc 'SUB_VIDEO' 'VIDEOIDLE' ([int]$snap.monitor_ac_s)
                Set-ActiveScheme
            }
            Step 'close Steam Big Picture' { Start-Process 'steam://close/bigpicture' }
            # DisplaySwitch has no reliable exit code; the manual check verifies the result.
            Step "display: restore (DisplaySwitch /$topology)" { & DisplaySwitch.exe "/$topology" }
            Step "delete snapshot $SnapshotPath" { Remove-Item $SnapshotPath }
            Out-Result $true ($(if ($DryRun) { 'on' } else { 'off' })) ''
        }
    }
}
catch {
    Out-Result $false 'error' $_.Exception.Message
}
