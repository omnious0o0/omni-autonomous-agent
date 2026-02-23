$ErrorActionPreference = "Stop"

function Write-Section($title) {
  Write-Output "----------------------------------------------------------------------"
  Write-Output "  $title"
  Write-Output "----------------------------------------------------------------------"
}

$scriptSourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptSourceDir "..")
$mainScript = Join-Path $rootDir "main.py"
$repoUrl = "https://github.com/omnious0o0/omni-autonomous-agent.git"
$installDir = $env:OMNI_AGENT_INSTALL_DIR
if (-not $installDir -or -not $installDir.Trim()) {
  $installDir = Join-Path $HOME ".omni-autonomous-agent"
}

$repoGitDir = Join-Path $installDir ".git"
$installScript = Join-Path $installDir ".omni-autonomous-agent\install.ps1"

if (-not (Test-Path $mainScript)) {
  Write-Section "Bootstrapping repository"

  $git = Get-Command git -ErrorAction SilentlyContinue
  if (-not $git) {
    Write-Error "git is required for install."
    exit 1
  }

  if (Test-Path $repoGitDir) {
    Write-Output "  Repository:  $installDir (existing, pulling latest)"
    & $git.Source -C $installDir pull --ff-only
    if ($LASTEXITCODE -ne 0) {
      Write-Error "failed to pull latest repository at $installDir"
      exit 1
    }
  }
  elseif (Test-Path $installDir) {
    Write-Error "$installDir exists but is not a git repository. Remove it manually or set OMNI_AGENT_INSTALL_DIR to a clean location."
    exit 1
  }
  else {
    Write-Output "  Repository:  Cloning to $installDir"
    & $git.Source clone $repoUrl $installDir
    if ($LASTEXITCODE -ne 0) {
      Write-Error "failed to clone repository to $installDir"
      exit 1
    }
  }

  if (-not (Test-Path $installScript)) {
    Write-Error "install script not found at $installScript after bootstrap"
    exit 1
  }

  $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($pwsh) {
    & $pwsh.Source -NoProfile -ExecutionPolicy Bypass -File $installScript
  }
  else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installScript
  }
  exit $LASTEXITCODE
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
set "PS_EXE=pwsh"
where pwsh >nul 2>&1 || set "PS_EXE=powershell"
%PS_EXE% -NoProfile -ExecutionPolicy Bypass -File "$runnerPs1" %*
exit /b %ERRORLEVEL%
"@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($runnerPs1, $runnerPs1Body, $utf8NoBom)
[System.IO.File]::WriteAllText($runnerCmd, $runnerCmdBody, $utf8NoBom)

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
