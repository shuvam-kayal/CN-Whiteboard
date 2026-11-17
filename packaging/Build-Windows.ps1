param(
    [string]$Python312Path = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if (-not $Python312Path -and $PythonLauncher) {
    & $PythonLauncher.Source -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)"
    if ($LASTEXITCODE -eq 0) { $Python312Path = "$($PythonLauncher.Source) -3.12" }
}
if (-not $Python312Path) {
    $Candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate) {
            & $Candidate -c "import sys; assert sys.version_info[:2] == (3, 12)"
            if ($LASTEXITCODE -eq 0) { $Python312Path = $Candidate; break }
        }
    }
}
if (-not $Python312Path) {
    throw "Python 3.12 is required. Install it or pass -Python312Path with its executable path. No other Python version will be substituted."
}

# A py-launcher selection is stored as the command plus its -3.12 selector.
$PythonCommand = $Python312Path
$PythonSelector = @()
if ($Python312Path -match "^(.*\\py\.exe) -3\.12$") {
    $PythonCommand = $Matches[1]
    $PythonSelector = @("-3.12")
}
& $PythonCommand @PythonSelector -c "import sys; print('Packaging Python:', sys.version); assert sys.version_info[:2] == (3, 12)"
if ($LASTEXITCODE -ne 0) { throw "The selected interpreter is not Python 3.12." }

Set-Location $ProjectRoot
$VenvDirectory = Join-Path $ProjectRoot ".venv-packaging"
$VenvPython = Join-Path $VenvDirectory "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $PythonCommand @PythonSelector -m venv $VenvDirectory
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv-packaging using Python 3.12." }
}
& $VenvPython -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version; print('Build environment:', sys.version)"
if ($LASTEXITCODE -ne 0) {
    throw ".venv-packaging exists but is not a Python 3.12 environment. Remove it manually or choose another clean path."
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not update pip in the packaging environment." }
& $VenvPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Could not install project and packaging dependencies." }

$PreviousQtPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $VenvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "The Python 3.12 test suite failed; packaging stopped." }
}
finally {
    if ($null -eq $PreviousQtPlatform) {
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    }
    else {
        $env:QT_QPA_PLATFORM = $PreviousQtPlatform
    }
}

$DistRoot = Join-Path $ProjectRoot "dist\pyinstaller"
$WorkRoot = Join-Path $ProjectRoot "build\pyinstaller"
New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null

& $VenvPython -m PyInstaller --clean --noconfirm --distpath $DistRoot --workpath (Join-Path $WorkRoot "server") (Join-Path $ProjectRoot "WhiteboardServer.spec")
if ($LASTEXITCODE -ne 0) { throw "WhiteboardServer PyInstaller build failed." }
& $VenvPython -m PyInstaller --clean --noconfirm --distpath $DistRoot --workpath (Join-Path $WorkRoot "client") (Join-Path $ProjectRoot "WhiteboardClient.spec")
if ($LASTEXITCODE -ne 0) { throw "WhiteboardClient PyInstaller build failed." }

$ServerBuild = Join-Path $DistRoot "WhiteboardServer"
$ClientBuild = Join-Path $DistRoot "WhiteboardClient"
$ServerExe = Join-Path $ServerBuild "WhiteboardServer.exe"
$ClientExe = Join-Path $ClientBuild "WhiteboardClient.exe"
if (-not (Test-Path -LiteralPath $ServerExe)) { throw "Server executable is missing: $ServerExe" }
if (-not (Test-Path -LiteralPath $ClientExe)) { throw "Client executable is missing: $ClientExe" }

$ReleaseRoot = Join-Path $ProjectRoot "dist\LAN-Whiteboard-Release"
$ResolvedDist = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "dist"))
$ResolvedRelease = [System.IO.Path]::GetFullPath($ReleaseRoot)
$RequiredPrefix = $ResolvedDist.TrimEnd('\') + '\'
if (-not $ResolvedRelease.StartsWith($RequiredPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean a release path outside dist: $ResolvedRelease"
}
if (Test-Path -LiteralPath $ResolvedRelease) {
    Remove-Item -LiteralPath $ResolvedRelease -Recurse -Force
}

$ReleaseServer = Join-Path $ResolvedRelease "Server"
$ReleaseClient = Join-Path $ResolvedRelease "Client"
New-Item -ItemType Directory -Path $ReleaseServer -Force | Out-Null
New-Item -ItemType Directory -Path $ReleaseClient -Force | Out-Null
Copy-Item -Path (Join-Path $ServerBuild "*") -Destination $ReleaseServer -Recurse
Copy-Item -Path (Join-Path $ClientBuild "*") -Destination $ReleaseClient -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\README-Server.txt") -Destination $ReleaseServer
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\README-Client.txt") -Destination $ReleaseClient

Write-Host "Build complete."
Write-Host "Server: $ReleaseServer\WhiteboardServer.exe"
Write-Host "Client: $ReleaseClient\WhiteboardClient.exe"
Write-Host "Release folder: $ResolvedRelease"
