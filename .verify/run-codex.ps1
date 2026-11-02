<#
.SYNOPSIS
  Read-only Codex review of a task: card + diff vs base + test output -> JSON verdict.
.EXAMPLE
  .\.verify\run-codex.ps1 -Task T-001 -Round 1
#>
param(
    [Parameter(Mandatory = $true)][string]$Task,
    [int]$Round = 1,
    [string]$Base = "main",
    [string]$TestOutput = ""
)
$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 pipes to native exes as ASCII by default; Cyrillic would become '?'.
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $OutputEncoding
if ($Round -gt 2) { throw "Max 2 rounds per task. Ask the leader." }

$root = git rev-parse --show-toplevel
Set-Location $root
$outDir = Join-Path $root ".verify/out"
New-Item -ItemType Directory -Force $outDir | Out-Null

$card = Join-Path $root "tasks/$Task.md"
if (-not (Test-Path $card)) { throw "No task card: tasks/$Task.md" }

# Make untracked files visible to `git diff` without staging their content.
git add -N . | Out-Null
$diff = (git diff $Base -- . ':(exclude)docs/architecture.md') -join "`n"

if (-not $TestOutput) {
    $TestOutput = Join-Path $outDir "$Task.tests.txt"
    python -m pytest -q -p no:cacheprovider 2>&1 | Out-File -Encoding utf8 $TestOutput
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
$prompt = [System.IO.File]::ReadAllText((Join-Path $root ".verify/reviewer-prompt.md"), $utf8)
$prompt += "`n`n## Task card`n" + [System.IO.File]::ReadAllText($card, $utf8)
$prompt += "`n`n## Test output`n" + [System.IO.File]::ReadAllText($TestOutput, $utf8)
$prompt += "`n`n## git diff $Base`n" + $diff

$promptFile = Join-Path $outDir "$Task.round-$Round.prompt.md"
[System.IO.File]::WriteAllText($promptFile, $prompt, $utf8)
$result = Join-Path $outDir "$Task.round-$Round.json"

Get-Content -Raw -Encoding UTF8 $promptFile | codex exec --sandbox read-only -C $root `
    --output-schema (Join-Path $root ".verify/verdict.schema.json") `
    -o $result --color never -
if ($LASTEXITCODE -ne 0) { throw "codex exec failed with exit code $LASTEXITCODE" }
Write-Host "Verdict: $result"
