# Build the patcher: python patcher/build_payload.py first, then this script.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
Import-Module (Join-Path $vs 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64'
Set-Location $root
New-Item -ItemType Directory -Force obj, release | Out-Null
rc /nologo /fo obj\patcher.res resources\patcher.rc
if ($LASTEXITCODE) { throw 'rc failed' }
cl /nologo /std:c++17 /EHsc /O2 /MT /utf-8 /W3 /Fo:obj\ /Fe:"release\DOA Paradise 60 FPS Patcher.exe" doap_patcher.cpp obj\patcher.res /link /SUBSYSTEM:WINDOWS gdiplus.lib shlwapi.lib bcrypt.lib winhttp.lib comdlg32.lib shell32.lib user32.lib gdi32.lib ole32.lib
if ($LASTEXITCODE) { throw 'cl failed' }
Get-FileHash "release\DOA Paradise 60 FPS Patcher.exe" -Algorithm SHA256
