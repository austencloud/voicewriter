# Builds dist\VoiceWriter-Setup-<version>.exe. Needs Inno Setup 6 and uv on PATH.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$version = (Select-String ..\voicewriter\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value

# uv.exe and its licenses ship inside the installer; copied fresh from this PC's uv.
$uv = (Get-Command uv).Source
New-Item -ItemType Directory -Force bin | Out-Null
Copy-Item $uv bin\uv.exe -Force
$licenses = Get-ChildItem (Split-Path (Split-Path $uv)) -Recurse -Filter 'LICENSE-*' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\uv-[\d.]+\.dist-info\\' }
if (-not $licenses) { throw "uv's license files not found next to $uv" }
foreach ($l in $licenses) { Copy-Item $l.FullName "bin\uv-$($l.Name)" -Force }

$iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
& $iscc /Qp "/DAppVersion=$version" voicewriter.iss
if ($LASTEXITCODE) { throw "ISCC failed ($LASTEXITCODE)" }
Get-Item "..\dist\VoiceWriter-Setup-$version.exe"
