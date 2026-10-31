$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
}
& '.\.venv\Scripts\python.exe' -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python version.' }
node -e "if(Number(process.versions.node.split('.')[0])<22)process.exit(1)"
if ($LASTEXITCODE -ne 0) { throw 'YouTube requires Node.js 22 or newer on PATH. Install Node, then run Setup again.' }
& '.\.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your connection to PyPI.' }
& '.\.venv\Scripts\python.exe' -c "import yt_dlp_ejs, imageio_ffmpeg; print('YouTube JavaScript scripts installed; FFmpeg:', imageio_ffmpeg.get_ffmpeg_exe())"
if ($LASTEXITCODE -ne 0) { throw 'YouTube runtime verification failed.' }
$statePath = Join-Path $PSScriptRoot '.local'
New-Item -ItemType Directory -Path $statePath -Force | Out-Null
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $statePath '/inheritance:r' '/grant:r' "*${currentSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict local session storage permissions.' }
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Verification failed. See the test output.' }
