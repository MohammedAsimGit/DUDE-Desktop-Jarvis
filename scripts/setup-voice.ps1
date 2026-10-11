<#
.SYNOPSIS
  One-time, explicit voice setup for Dude on Windows.

.DESCRIPTION
  Installs the optional voice dependencies into the repository's .venv and
  downloads a local VOSK speech-recognition model to the exact path the
  installed SpeechRecognition package looks for it. Nothing is downloaded
  when Dude starts - only when you run this script.

  Nothing is written outside the repository's .venv (model files live in
  .venv\Lib\site-packages\speech_recognition\models\vosk, which is inside
  the git-ignored .venv, so model files can never be committed).

.PARAMETER ModelUrl
  Official VOSK model zip to download. Defaults to the small English model
  (vosk-model-small-en-us-0.15, ~40 MB download, a few hundred MB of runtime
  memory). The model you choose determines the spoken language Dude can
  transcribe. Browse https://alphacephei.com/vosk/models for other languages
  and pass the zip URL here.

.PARAMETER SkipModel
  Install/refresh dependencies only; do not download a model.

.EXAMPLE
  .\scripts\setup-voice.ps1

.EXAMPLE
  .\scripts\setup-voice.ps1 -ModelUrl "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip"
#>
[CmdletBinding()]
param(
    [string]$ModelUrl = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
    [switch]$SkipModel
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "SETUP FAILED: $Message" -ForegroundColor Red
    exit 1
}

# Run from the repository root regardless of where the script was invoked.
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $RepoRoot "engine"))) {
    Fail "could not locate the repository root from '$PSScriptRoot' (engine\ not found)."
}
Set-Location $RepoRoot
Write-Host "Repository root: $RepoRoot"

# 1) Python for the repository virtual environment -------------------------
$PythonLauncher = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonLauncher) {
    Fail "python was not found on PATH. Install Python 3.11+ and re-run this script."
}
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating .venv ..."
    & $PythonLauncher.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "creating the virtual environment failed (python -m venv exited $LASTEXITCODE)." }
} else {
    Write-Host "Using existing .venv"
}

if (-not (Test-Path $VenvPython)) {
    Fail "expected interpreter '$VenvPython' was not created."
}

# 2) Project-level voice dependencies only (never system-wide) -------------
Write-Host "Installing voice dependencies into .venv ..."
& $VenvPython -m pip install --upgrade -r (Join-Path $RepoRoot "engine\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "dependency installation failed (pip exited $LASTEXITCODE). Check network access and re-run." }

# 3) Confirm the required imports resolve in THIS interpreter --------------
Write-Host "Verifying voice dependencies ..."
$verify = @'
import importlib, sys
missing = []
for mod in ('speech_recognition', 'pyaudio', 'vosk', 'pyttsx3', 'tqdm'):
    try:
        importlib.import_module(mod)
    except Exception as exc:
        missing.append('%s: %s' % (mod, exc))
if missing:
    sys.stderr.write('MISSING IMPORTS:\n')
    for line in missing:
        sys.stderr.write(' - %s\n' % line)
    sys.exit(1)
import speech_recognition as sr
print('SpeechRecognition', sr.__version__)
'@
& $VenvPython -c $verify
if ($LASTEXITCODE -ne 0) { Fail "voice imports are not resolvable in .venv. Re-run after fixing the pip output above." }

if ($SkipModel) {
    Write-Host "Skipping model download (-SkipModel)." -ForegroundColor Yellow
    exit 0
}

# 4) Download the VOSK model to the exact path the recognizer uses ---------
# The installed package resolves the model relative to its own files:
#   <site-packages>/speech_recognition/models/vosk
# `sprc download vosk` writes exactly there (confirmed against the installed
# recognizers/vosk.py). We never guess a project-local model directory.
$ModelTarget = & $VenvPython -c "from pathlib import Path; import speech_recognition as vr; print(Path(vr.__file__).parent / 'models' / 'vosk')"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ModelTarget)) {
    Fail "could not resolve the VOSK model path from the installed SpeechRecognition package."
}
$ModelTarget = "$ModelTarget".Trim()

if (Test-Path $ModelTarget) {
    Write-Host "A VOSK model already exists at:`n  $ModelTarget" -ForegroundColor Yellow
    $answer = Read-Host "Replace it with '$ModelUrl'? [y/N]"
    if ($answer -notmatch '^[Yy]') {
        Write-Host "Keeping the existing model. Done." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host "Downloading VOSK model (this can take a minute on a slow connection) ..."
& $VenvPython -m speech_recognition.cli download vosk --url $ModelUrl
if ($LASTEXITCODE -ne 0) {
    Fail "model download failed (sprc exited $LASTEXITCODE). Check network access, or download the model manually from https://alphacephei.com/vosk/models and extract it to:`n  $ModelTarget"
}

if (-not (Test-Path $ModelTarget)) {
    Fail "the download command reported success but the model is not at the expected path:`n  $ModelTarget"
}
# A usable VOSK model directory contains an 'am' folder with model files.
if (-not (Test-Path (Join-Path $ModelTarget "am"))) {
    Fail "the directory '$ModelTarget' does not look like a VOSK model (missing 'am'). Re-run this script, or install a model manually per https://github.com/alphacephei/vosk#model-installation."
}

# 5) Confirm the recognizer itself can load the model ----------------------
Write-Host "Verifying the recognizer can load the model ..."
$loadCheck = @'
from pathlib import Path
from speech_recognition.recognizers import vosk as vosk_recognizer
path = Path(vosk_recognizer.__file__).parent.parent / 'models' / 'vosk'
if not path.exists():
    raise SystemExit('model missing at %s' % path)
from vosk import Model
Model(str(path))
print('VOSK model OK at', path)
'@
& $VenvPython -c $loadCheck
if ($LASTEXITCODE -ne 0) { Fail "the VOSK model could not be loaded from '$ModelTarget'. Delete that folder and re-run this script." }

Write-Host ""
Write-Host "Voice setup complete." -ForegroundColor Green
Write-Host "  Model: $ModelTarget"
Write-Host "  Next: launch with the venv interpreter, e.g."
Write-Host '    $env:DUDE_PYTHON = "$PWD\.venv\Scripts\python.exe"; npm run tauri dev'
