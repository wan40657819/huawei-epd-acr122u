#requires -RunAsAdministrator
# One-time ACR122U setup for raw PN532 command forwarding (InCommunicateThru,
# register access).  Windows' Microsoft Usbccid driver blocks SCardControl
# escape commands until EscapeCommandEnable=1 exists for the device instance.
# Run this script as Administrator, then physically unplug and replug the
# ACR122U (a driver restart can leave the device in a Code 43 USB state).

$ErrorActionPreference = 'Stop'
$base = 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB\VID_072F&PID_2200'

if (-not (Test-Path $base)) {
    Write-Error "ACR122U registry node not found: $base"
    exit 1
}

$changed = 0
Get-ChildItem -Path $base | ForEach-Object {
    $parameters = Join-Path $_.PSPath 'Device Parameters'
    if (Test-Path $parameters) {
        New-ItemProperty -Path $parameters -Name 'EscapeCommandEnable' `
            -PropertyType DWord -Value 1 -Force | Out-Null
        $driver = Join-Path $parameters 'WudfUsbccidDriver'
        New-Item -Path $driver -Force | Out-Null
        New-ItemProperty -Path $driver -Name 'EscapeCommandEnable' `
            -PropertyType DWord -Value 1 -Force | Out-Null
        Write-Host "Enabled: $parameters"
        $changed++
    }
}

if ($changed -eq 0) {
    Write-Error 'No ACR122U Device Parameters key was found. Plug the reader in and rerun.'
    exit 1
}

Write-Host ''
Write-Host 'Done. Unplug and replug the ACR122U, then select it in the application.'
