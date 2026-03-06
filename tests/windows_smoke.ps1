$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("oaa-win-smoke-" + [guid]::NewGuid().ToString("N"))

function Write-CmdShim {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Body
  )

  $path = Join-Path $script:fakeBin ($Name + ".cmd")
  [System.IO.File]::WriteAllText($path, $Body, [System.Text.UTF8Encoding]::new($false))
}

try {
  $homeDir = Join-Path $workDir "home"
  $localAppData = Join-Path $workDir "localappdata"
  $appData = Join-Path $workDir "appdata"
  $binDir = Join-Path $workDir "bin"
  $script:fakeBin = Join-Path $workDir "fakebin"
  $wrapperBin = Join-Path $workDir "wrappers"
  $opencodeConfig = Join-Path $workDir "opencode-config"

  foreach ($dir in @($homeDir, $localAppData, $appData, $binDir, $script:fakeBin, $wrapperBin, $opencodeConfig)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }

  $env:HOME = $homeDir
  $env:USERPROFILE = $homeDir
  $env:LOCALAPPDATA = $localAppData
  $env:APPDATA = $appData
  $env:OMNI_AGENT_LOCAL_BIN = $binDir
  $env:OMNI_AGENT_INSTALL_DIR = Join-Path $workDir "install-root"
  $env:OMNI_AGENT_CONFIG_DIR = Join-Path $workDir "config"
  $env:OMNI_AGENT_SANDBOX_ROOT = Join-Path $workDir "sandbox"
  $env:OMNI_AGENT_REPO_ROOT = $root
  $env:OMNI_AGENT_DISABLE_AUTO_UPDATE = "1"
  $env:OMNI_AGENT_WRAPPER_BIN = $wrapperBin
  $env:OMNI_AGENT_OPENCODE_PLUGIN = Join-Path $opencodeConfig "plugins" "omni-hook.ts"
  $env:PATH = "$script:fakeBin;$env:PATH"

  $simpleShim = @'
@echo off
if "%~1"=="--exit-code" exit /b %~2
if "%~1"=="--version" (
  echo 0.0.0-test
  exit /b 0
)
exit /b 0
'@

  foreach ($name in @("codex", "gemini", "opencode", "futureagent")) {
    Write-CmdShim -Name $name -Body $simpleShim
  }

  $openclawShim = @'
@echo off
if "%~1"=="--version" (
  echo 2026.3.2-test
  exit /b 0
)
if "%~1"=="hooks" if "%~2"=="enable" exit /b 0
if "%~1"=="hooks" if "%~2"=="check" (
  echo omni-recovery ok
  echo session-memory ok
  exit /b 0
)
if "%~1"=="hooks" if "%~2"=="list" (
  echo omni-recovery enabled
  echo session-memory enabled
  exit /b 0
)
if "%~1"=="hooks" if "%~2"=="info" if "%~3"=="omni-recovery" (
  echo name: omni-recovery
  echo events:
  echo   - gateway:startup
  echo   - message:received
  echo   - message:transcribed
  echo   - message:preprocessed
  echo   - session:compact:before
  exit /b 0
)
exit /b 0
'@
  Write-CmdShim -Name "openclaw" -Body $openclawShim

  & (Join-Path $root ".omni-autonomous-agent/install.ps1") | Out-Null

  $runnerPs1 = Join-Path $binDir "omni-autonomous-agent.ps1"
  $runnerCmd = Join-Path $binDir "omni-autonomous-agent.cmd"
  if (-not (Test-Path $runnerPs1)) { throw "windows-smoke failed: missing omni-autonomous-agent.ps1" }
  if (-not (Test-Path $runnerCmd)) { throw "windows-smoke failed: missing omni-autonomous-agent.cmd" }

  $statusOutput = & $runnerPs1 --status 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0) { throw "windows-smoke failed: --status returned $LASTEXITCODE" }
  if ($statusOutput -notmatch "No active session") { throw "windows-smoke failed: --status missing expected output" }

  if (-not (Test-Path (Join-Path $homeDir ".gemini/settings.json"))) { throw "windows-smoke failed: missing Gemini settings" }
  if (-not (Test-Path (Join-Path $opencodeConfig "plugins/omni-hook.ts"))) { throw "windows-smoke failed: missing OpenCode plugin" }
  if (-not (Test-Path (Join-Path $homeDir ".openclaw/hooks/omni-recovery/HOOK.md"))) { throw "windows-smoke failed: missing OpenClaw hook" }
  if (-not (Test-Path (Join-Path $wrapperBin "omni-wrap-codex.cmd"))) { throw "windows-smoke failed: missing codex wrapper" }
  if (-not (Test-Path (Join-Path $wrapperBin "omni-agent-wrap.cmd"))) { throw "windows-smoke failed: missing universal wrapper" }

  & (Join-Path $wrapperBin "omni-wrap-codex.cmd") --version *> $null
  if ($LASTEXITCODE -ne 3) { throw "windows-smoke failed: codex wrapper preflight expected 3, got $LASTEXITCODE" }

  $env:AGENT = "futureagent"
  & $runnerPs1 --bootstrap | Out-Null
  Remove-Item Env:AGENT
  if (-not (Test-Path (Join-Path $wrapperBin "omni-wrap-futureagent.cmd"))) { throw "windows-smoke failed: missing futureagent wrapper" }

  & $runnerPs1 --add -R "windows smoke" -D dynamic | Out-Null
  $hookOutput = & $runnerPs1 --hook-stop 2>&1 | Out-String
  $hookCode = $LASTEXITCODE
  if ($hookCode -ne 2) { throw "windows-smoke failed: hook-stop expected 2, got $hookCode" }
  if ($hookOutput -notmatch '"template_id": "stop-blocked"') { throw "windows-smoke failed: hook-stop output missing stop-blocked template" }

  Write-Output "windows-smoke passed"
}
finally {
  Remove-Item -Recurse -Force $workDir -ErrorAction SilentlyContinue
}
