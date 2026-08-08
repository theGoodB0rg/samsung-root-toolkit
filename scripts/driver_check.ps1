<#
.SYNOPSIS
    Diagnose why adb can't see the phone.

.DESCRIPTION
    Prints host facts (Python, adb, Samsung PnP entities, driver store,
    COM ports, admin rights) and the most likely fix. Exits 0 when a device
    is visible on adb, 1 otherwise.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\driver_check.ps1
#>
$ErrorActionPreference = "Continue"

Write-Host "=== SRTK driver check ===" -ForegroundColor Cyan

Write-Host "`n-- python --"
try { python --version 2>&1 } catch { Write-Host "python not found" }

Write-Host "`n-- adb --"
try {
    adb version
    adb kill-server | Out-Null
    adb start-server | Out-Null
    adb devices -l
} catch {
    Write-Host "adb not found on PATH - run scripts\bootstrap.ps1"
}

Write-Host "`n-- Samsung USB entities (PnP) --"
$entities = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'SAMSUNG|Samsung' } |
    ForEach-Object { $_.Name }
if ($entities) { $entities | ForEach-Object { Write-Host "  PnP: $_" } }
else { Write-Host "  none - Samsung driver not loaded" }

Write-Host "`n-- Samsung driver store packages --"
$drivers = pnputil /enum-drivers |
    Select-String -Pattern 'SAMSUNG|ssud|ssadadb' -CaseSensitive:$false |
    ForEach-Object { $_.Line }
if ($drivers) { $drivers | ForEach-Object { Write-Host "  Driver: $_" } }
else { Write-Host "  none - install SAMSUNG_USB_Driver (see bootstrap)" }

Write-Host "`n-- COM ports --"
try { [System.IO.Ports.SerialPort]::GetPortNames() } catch {}

Write-Host "`n-- privileges --"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())
    .IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "  admin: $isAdmin"

Write-Host "`n-- hints --"
Write-Host "  no device but PnP sees Samsung   : charging-only cable or USB debugging off"
Write-Host "  device shows 'unauthorized'       : accept the RSA prompt on the phone"
Write-Host "  device shows 'offline'            : unplug/replug, or adb kill-server"
Write-Host "  nothing in Download Mode          : check Odin driver (COM port missing)"

$visible = (& adb devices 2>$null | Select-String -Pattern '\tdevice').Count -gt 0
Write-Host ""
if ($visible) {
    Write-Host "OK: a device is visible on adb." -ForegroundColor Green
    exit 0
}
Write-Host "NOT OK: no device on adb - see hints above." -ForegroundColor Red
exit 1
