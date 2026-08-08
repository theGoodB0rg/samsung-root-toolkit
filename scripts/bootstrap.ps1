<#
.SYNOPSIS
    Install / verify SRTK host tooling.

.DESCRIPTION
    Installs, into tools\bin\, the binaries SRTK needs:
      - platform-tools (adb, fastboot)     from dl.google.com
      - samloader-rs                        from topjohnwu GitHub releases
      - scrcpy                              from genymobile GitHub releases
      - Samsung USB driver + Odin3          manual (no stable auto source) - instructions printed
    Downloads the module assets into src\srtk\modules\:
      - Magisk.apk, PlayIntegrityFork.zip, TrickyStore.zip, IntegrityChecker.apk
      (best-effort from GitHub releases; manual placement instructions printed
      if a source is unavailable)
    Optionally installs RustDesk for the remote session.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1

    Run from an elevated (Administrator) PowerShell so driver installs work.
#>
[CmdletBinding()]
param(
    [switch]$SkipDownloads,      # keep existing downloads; only print status
    [switch]$SkipRustDesk
)
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Bin  = Join-Path $Root "tools\bin"
$Mods = Join-Path $Root "src\srtk\modules"
New-Item -ItemType Directory -Force -Path $Bin, $Mods | Out-Null

$status = [ordered]@{}
function Step([string]$Name, [scriptblock]$Body) {
    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    try {
        & $Body
        $status[$Name] = "ok"
        Write-Host "   ok" -ForegroundColor Green
    } catch {
        $status[$Name] = "FAILED: $($_.Exception.Message)"
        Write-Host "   FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Get-File([string]$Url, [string]$Out) {
    if (Test-Path $Out) { Write-Host "   cached: $Out"; return }
    curl.exe -fL --retry 3 -o $Out $Url
    if ($LASTEXITCODE -ne 0) { throw "download failed (curl rc=$LASTEXITCODE): $Url" }
    Write-Host "   downloaded: $Out"
}

function Get-GitHubAsset([string]$Repo, [string]$Pattern, [string]$Out) {
    $release = Invoke-RestMethod -Headers @{ "User-Agent" = "srtk-bootstrap" } `
        -Uri "https://api.github.com/repos/$Repo/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -match $Pattern } | Select-Object -First 1
    if (-not $asset) { throw "no asset matching '$Pattern' in $($Repo) $($release.tag_name)" }
    Get-File $asset.browser_download_url $Out
}

function Expand-Zip([string]$Zip, [string]$Dest, [string]$Match = "*") {
    $tmp = Join-Path (Split-Path $Zip) ("_" + [IO.Path]::GetFileNameWithoutExtension($Zip))
    if (-not (Test-Path $tmp)) { Expand-Archive -Force -Path $Zip -DestinationPath $tmp }
    $hit = Get-ChildItem -Recurse -Path $tmp -Filter $Match | Select-Object -First 1
    if (-not $hit) { throw "no file matching '$Match' in $Zip" }
    Copy-Item $hit.FullName -Destination $Dest -Force
    Write-Host "   installed: $($hit.Name) -> $Dest"
}

if (-not $SkipDownloads) {
    Step "platform-tools (adb/fastboot)" {
        $zip = Join-Path $Bin "platform-tools.zip"
        Get-File "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" $zip
        Expand-Zip $zip $Bin "adb.exe"
        Expand-Zip $zip $Bin "fastboot.exe"
    }
    Step "samloader-rs" {
        $zip = Join-Path $Bin "samloader.zip"
        Get-GitHubAsset "topjohnwu/samloader-rs" "windows.*\.zip$" $zip
        Expand-Zip $zip $Bin "samloader*.exe"
    }
    Step "scrcpy" {
        $zip = Join-Path $Bin "scrcpy.zip"
        Get-GitHubAsset "genymobile/scrcpy" "scrcpy-win64-.*\.zip$" $zip
        Expand-Zip $zip $Bin "scrcpy.exe"
        Expand-Zip $zip $Bin "scrcpy-server*"
    }
    Step "RustDesk (remote session)" {
        if ($SkipRustDesk) { throw "skipped by -SkipRustDesk" }
        $exe = Get-Command winget -ErrorAction SilentlyContinue
        if ($exe) {
            winget install --id RustDesk.RustDesk --accept-source-agreements --accept-package-agreements | Out-Null
            Write-Host "   installed via winget"
        } else {
            $installer = Join-Path $Bin "rustdesk.exe"
            Get-GitHubAsset "rustdesk/rustdesk" "rustdesk-.*-x86_64\.exe$" $installer
        }
    }
}

# Manual-source components (no stable official URL): print exact steps.
Write-Host ""
Write-Host "== Manual components (read the notes) ==" -ForegroundColor Yellow
Write-Host @"
  Samsung USB driver : download 'SAMSUNG_USB_Driver_for_Mobile_Phones.exe'
        from https://developer.samsung.com/android-usb-driver (or a mirror).
        Then run it as Administrator, or extract + `pnputil /add-driver *.inf`.
  Odin3              : download Odin3 v3.14.x (the Magisk-docs link is a known
        source). Place Odin3.exe in tools\bin\.
  Adb auth           : after the driver is installed, plug the phone in and
        accept the RSA prompt; `adb devices` should list it as 'device'.
"@

Step "module assets (Magisk / PIF / Tricky Store / Integrity Checker)" {
    if ($SkipDownloads) { throw "skipped by -SkipDownloads" }
    Get-GitHubAsset "topjohnwu/Magisk" "Magisk-v[0-9.]+-apk\.apk$" (Join-Path $Mods "Magisk.apk")
    Get-GitHubAsset "osm0sis/PlayIntegrityFork" "PlayIntegrityFork-v[0-9.]+\.zip$" (Join-Path $Mods "PlayIntegrityFork.zip")
    Get-GitHubAsset "5ec1cff/TrickyStore" "TrickyStore-.*\.zip$" (Join-Path $Mods "TrickyStore.zip")
    Get-GitHubAsset "nikolasspyr/integritycheck" "\.apk$" (Join-Path $Mods "IntegrityChecker.apk")
}

Write-Host ""
Write-Host "== Summary ==" -ForegroundColor Cyan
foreach ($k in $status.Keys) { Write-Host ("  {0,-28} {1}" -f $k, $status[$k]) }

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Place any missing module files into: $Mods"
Write-Host "  2. Run scripts\driver_check.ps1 with the phone plugged in."
Write-Host "  3. Start the remote session, then `python -m srtk run all`."
Write-Host ""
