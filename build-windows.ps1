$ErrorActionPreference = "Stop"

$project = $PSScriptRoot
$python = Join-Path $project ".venv\Scripts\python.exe"
$main = Join-Path $project "src\main.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Windows virtual environment not found. Run: py -3.11 -m venv .venv"
}

$versionMatch = Select-String -LiteralPath $main -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' |
    Select-Object -First 1
if (-not $versionMatch) {
    throw "APP_VERSION was not found in src\main.py"
}
$version = $versionMatch.Matches[0].Groups[1].Value
$name = "ColourSpacePatchRx-$version-windows"

$assets = Join-Path $project "assets"
$icon = Join-Path $assets "icon.ico"
$license = Join-Path $project "LICENSE"
$thirdPartyNotices = Join-Path $project "THIRD_PARTY_NOTICES.md"
$source = Join-Path $project "src"
$work = Join-Path $project "build\windows"
$spec = Join-Path $project "build\windows-spec"
$dist = Join-Path $project "dist\windows"

Write-Host "Building $name.exe"
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $name `
    --icon $icon `
    --add-data "$assets;assets" `
    --add-data "$license;." `
    --add-data "$thirdPartyNotices;." `
    --paths $source `
    --workpath $work `
    --specpath $spec `
    --distpath $dist `
    $main

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $dist "$name.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Expected executable was not created: $exe"
}

$hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLower()
$checksum = Join-Path $dist "SHA256-windows.txt"
"$hash  $name.exe" | Set-Content -LiteralPath $checksum -Encoding UTF8

Write-Host "Build complete: $exe"
Write-Host "SHA-256: $hash"
