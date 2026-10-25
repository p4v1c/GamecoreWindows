# Bluetooth wake: the radio and the Bluetooth HID devices paired on it may wake
# the PC ("Allow this device to wake the computer"), as the radio's wakeup and
# BlueZ's WakeAllowed did on Linux: a pad pages the box through its radio.
# The backend arms pads paired later at run time (backend/services/bt_wake.py);
# this step makes the radio's setting part of the install, noted for uninstall.
# A Modern Standby PC may list no setting for its radio: Windows then keeps it
# up for input devices by itself. Keyboards and mice keep their rights (D36).

$script:GcWakeRadioBuses = @('USB\', 'PCI\')
# The Bluetooth HID service (classic) and HID over GATT (LE).
$script:GcWakeBluetoothHid = @('BTHENUM\{00001124-', 'BTHLEDEVICE\{00001812-')

function Get-GcWakeDevice {
    <# {Name (WMI instance), Instance, Class, Enabled} of every device able to wake the PC. #>
    $classes = @{}
    foreach ($entity in (Get-CimInstance -ClassName Win32_PnPEntity)) {
        if ($entity.DeviceID) { $classes[$entity.DeviceID.ToUpperInvariant()] = "$($entity.PNPClass)" }
    }
    $devices = @()
    foreach ($row in (Get-CimInstance -Namespace 'root\wmi' -ClassName MSPower_DeviceWakeEnable)) {
        $instance = $row.InstanceName -replace '_\d+$', ''
        $devices += [pscustomobject]@{ Name = $row.InstanceName; Instance = $instance
            Class = $classes[$instance.ToUpperInvariant()]; Enabled = [bool]$row.Enable }
    }
    return $devices
}

function Test-GcBluetoothWakeTarget {
    <# A Bluetooth radio on USB or PCI, or a Bluetooth HID device. #>
    param([Parameter(Mandatory)]$Device)
    $instance = $Device.Instance.ToUpperInvariant()
    if ($Device.Class -eq 'Bluetooth') {
        foreach ($bus in $script:GcWakeRadioBuses) { if ($instance.StartsWith($bus)) { return $true } }
    }
    foreach ($prefix in $script:GcWakeBluetoothHid) { if ($instance.StartsWith($prefix)) { return $true } }
    return $false
}

function Set-GcDeviceWake {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][bool]$Enabled)
    $row = Get-CimInstance -Namespace 'root\wmi' -ClassName MSPower_DeviceWakeEnable |
        Where-Object { $_.InstanceName -eq $Name }
    if (-not $row) { throw "no wake setting for $Name" }
    Set-CimInstance -InputObject $row -Property @{ Enable = $Enabled }
}

function Install-GcBluetoothWake {
    try {
        $devices = @(Get-GcWakeDevice)
    } catch {
        # Unelevated, WMI refuses: a dry run from a normal prompt lands here.
        Write-GcWarning "wake settings unreadable: $($_.Exception.Message)"
        return
    }
    $targets = @($devices | Where-Object { Test-GcBluetoothWakeTarget $_ })
    if (-not @($targets | Where-Object { $_.Class -eq 'Bluetooth' }).Count) {
        Write-GcInfo 'no Bluetooth radio with a wake setting (Modern Standby keeps it up for input devices)'
    }
    foreach ($target in @($targets | Where-Object { -not $_.Enabled })) {
        Set-GcRecord 'allow-bluetooth-wake' $target.Name $false
        Invoke-GcChange "allow $($target.Instance) to wake the PC" { Set-GcDeviceWake $target.Name $true }
    }
}

function Uninstall-GcBluetoothWake {
    param($Record)
    if ($null -eq $Record) { return }
    foreach ($name in @($Record.Keys)) {
        Invoke-GcChange "forbid $name to wake the PC again" {
            try {
                Set-GcDeviceWake $name $false
            } catch {
                # A device unpaired since the install has nothing left to put back.
                Write-GcWarning "could not put back the wake setting of ${name}: $($_.Exception.Message)"
            }
        }
    }
}
