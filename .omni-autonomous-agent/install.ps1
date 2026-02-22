$ErrorActionPreference = "Stop"

function Write-Section($title) {
  Write-Output "----------------------------------------------------------------------"
  Write-Output "  $title"
  Write-Output "----------------------------------------------------------------------"
}

$scriptSourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptSourceDir "..")
$mainScript = Join-Path $rootDir "main.py"

if (-not (Test-Path $mainScript)) {
  Write-Error "main.py not found at $mainScript"
  exit 1
}

$destName = "omni-autonomous-agent"
$localBin = $env:OMNI_AGENT_LOCAL_BIN
if (-not $localBin -or -not $localBin.Trim()) {
  if ($env:LOCALAPPDATA -and $env:LOCALAPPDATA.Trim()) {
    $localBin = Join-Path $env:LOCALAPPDATA "omni-autonomous-agent\bin"
  }
  else {
    $localBin = Join-Path $HOME "AppData\Local\omni-autonomous-agent\bin"
  }
}

New-Item -ItemType Directory -Force -Path $localBin | Out-Null

$runnerPs1 = Join-Path $localBin "$destName.ps1"
$runnerCmd = Join-Path $localBin "$destName.cmd"

$runnerPs1Body = @"

$ErrorActionPreference = "Stop"
$mainScript = "$($mainScript -replace '\\', '\\\\')"

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
  & $python.Source $mainScript @args
  exit $LASTEXITCODE
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
  & $py.Source -3 $mainScript @args
  exit $LASTEXITCODE
}

Write-Error "python runtime not found on PATH. Install Python 3 first."
exit 1
"@

$runnerCmdBody = @"
@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "$runnerPs1" %*
exit /b %ERRORLEVEL%
"@

Set-Content -Path $runnerPs1 -Value $runnerPs1Body -Encoding ascii
Set-Content -Path $runnerCmd -Value $runnerCmdBody -Encoding ascii

Write-Section "Installed"
Write-Output "  Source:      $mainScript"
Write-Output "  Command:     $runnerCmd"
Write-Output "  Self-check:  Read $rootDir\install-help.md"

& $runnerCmd --bootstrap *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Error "bootstrap did not complete successfully. Run '$runnerCmd --bootstrap' and fix reported warnings before autonomous use."
  exit 1
}

Write-Output "  Bootstrap:   Completed automatic hook/setup bootstrap"
Write-Output ""
Write-Output "Run $destName --status to verify."
Write-Output "Read install-help.md and apply the self-check hook guidance before autonomous sessions."

$pathValue = [string]$env:PATH
if ($pathValue -notmatch [regex]::Escape($localBin)) {
  Write-Output ""
  Write-Output "Warning: $localBin is not on PATH."
  Write-Output "Add this line in PowerShell profile:"
  Write-Output "  `$env:PATH = '$localBin;' + `$env:PATH"
}

Write-Output "----------------------------------------------------------------------"
