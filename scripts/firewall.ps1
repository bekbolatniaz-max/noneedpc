<#
.SYNOPSIS
  Allow Nitro Console (TCP port, default 8765) from the LOCAL PRIVATE network only.
.DESCRIPTION
  add    - create one inbound Allow rule: TCP <Port>, Profile=Private, LocalSubnet only.
  remove - delete that rule (rollback).
  status - show the rule if present.
  -DryRun prints what would change and changes nothing. add/remove need an elevated (admin) shell.
  Public networks (cafe, hotel) are never opened.
.EXAMPLE
  .\scripts\firewall.ps1 -Action add -DryRun
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('add', 'remove', 'status')][string]$Action,
    [int]$Port = 8765,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$name = "Nitro Console (TCP $Port, Private LAN)"
$existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

switch ($Action) {
    'status' {
        if ($existing) { $existing | Select-Object DisplayName, Enabled, Profile, Direction, Action | Format-List }
        else { "rule '$name' not found" }
    }
    'add' {
        if ($existing) { "already exists: '$name'"; break }
        $plan = "create inbound rule '$name': Allow TCP $Port, Profile=Private, RemoteAddress=LocalSubnet"
        if ($DryRun) { "[dry-run] $plan"; break }
        if (-not (Test-Admin)) { throw 'run this from an elevated (Administrator) PowerShell' }
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort $Port `
            -Profile Private -RemoteAddress LocalSubnet -Action Allow | Out-Null
        "done: $plan"
    }
    'remove' {
        if (-not $existing) { "nothing to remove"; break }
        if ($DryRun) { "[dry-run] delete rule '$name'"; break }
        if (-not (Test-Admin)) { throw 'run this from an elevated (Administrator) PowerShell' }
        Remove-NetFirewallRule -DisplayName $name
        "removed '$name'"
    }
}
