# Windows power settings a console needs, in every power plan (the sleep stage
# switches to Power saver, dev log D30):
#   power button  = sleep: Rest mode's sweep runs on any sleep (D35)
#   wake timers   = off: Windows Update maintenance would wake the box at night
#   password on wake = off: the pad cannot type one
#   USB selective suspend = off: it drops wired pads
#   Fast Startup  = off: it breaks sleep/wake and Bluetooth after a shutdown
# Values are read through WMI (language-neutral; powercfg's text is translated)
# and written with powercfg; the old ones are noted for uninstall.

$script:GcPowerSettings = @(
    [pscustomobject]@{ Name = 'power button action'; Group = '4f971e89-eebd-4455-a8de-9e59040e7347'; Setting = '7648efa3-dd9c-4e3e-b566-50f929386280'; AC = 1; DC = 1 }
    [pscustomobject]@{ Name = 'allow wake timers'; Group = '238c9fa8-0aad-41ed-83f4-97be242c8f20'; Setting = 'bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d'; AC = 0; DC = 0 }
    [pscustomobject]@{ Name = 'require a password on wake'; Group = 'fea3413e-7e05-4911-9a71-700331f1c294'; Setting = '0e796bdb-100d-47d6-a2d5-f7d2daa51f51'; AC = 0; DC = 0 }
    [pscustomobject]@{ Name = 'USB selective suspend'; Group = '2a737441-1930-4402-8d77-b2bebba308a3'; Setting = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'; AC = 0; DC = 0 }
)
$script:GcFastStartupKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'

function Get-GcPowerValue {
    <# Every (plan, AC|DC, setting) index Windows stores for the settings above. #>
    $wanted = @($script:GcPowerSettings | ForEach-Object { $_.Setting })
    $values = @()
    foreach ($entry in (Get-CimInstance -Namespace 'root\cimv2\power' -ClassName Win32_PowerSettingDataIndex)) {
        if ($entry.InstanceID -notmatch '\{([0-9a-f-]+)\}\\(AC|DC)\\\{([0-9a-f-]+)\}$') { continue }
        if ($wanted -notcontains $Matches[3]) { continue }
        $values += [pscustomobject]@{ Plan = $Matches[1]; Source = $Matches[2]; Setting = $Matches[3]
            Value = [int]$entry.SettingIndexValue }
    }
    return $values
}

function Get-GcPowerChange {
    <# The values that differ from what GameCore wants, as {Plan, Source, Setting, Group, Value, Wanted}. #>
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Current)
    $changes = @()
    foreach ($value in $Current) {
        $spec = $script:GcPowerSettings | Where-Object { $_.Setting -eq $value.Setting }
        $wanted = $spec.($value.Source)
        if ($value.Value -ne $wanted) {
            $changes += [pscustomobject]@{ Plan = $value.Plan; Source = $value.Source; Setting = $value.Setting
                Group = $spec.Group; Name = $spec.Name; Value = $value.Value; Wanted = $wanted }
        }
    }
    return $changes
}

function Set-GcPowerValue {
    param([Parameter(Mandatory)][string]$Plan, [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Group, [Parameter(Mandatory)][string]$Setting, [Parameter(Mandatory)][int]$Value)
    $verb = if ($Source -eq 'AC') { '/setacvalueindex' } else { '/setdcvalueindex' }
    Invoke-GcNative 'powercfg.exe' @($verb, $Plan, $Group, $Setting, "$Value") | Out-Null
}

function Install-GcPower {
    $changes = @(Get-GcPowerChange -Current @(Get-GcPowerValue))
    foreach ($change in $changes) {
        $key = "$($change.Plan)|$($change.Source)|$($change.Group)|$($change.Setting)"
        Set-GcRecord 'configure-power' $key $change.Value
        Invoke-GcChange "$($change.Name) = $($change.Wanted) ($($change.Source), plan $($change.Plan))" {
            Set-GcPowerValue $change.Plan $change.Source $change.Group $change.Setting $change.Wanted
        }
    }
    if ($changes.Count) {
        # The active plan reads its values again only when it is re-applied.
        Invoke-GcChange 'apply the active plan again' { Invoke-GcNative 'powercfg.exe' @('/setactive', 'SCHEME_CURRENT') | Out-Null }
    }
    $fast = Get-GcRegistryValue $script:GcFastStartupKey 'HiberbootEnabled'
    if ($fast -ne 0) {
        Set-GcRecord 'configure-power' 'HiberbootEnabled' $fast
        Invoke-GcChange 'turn Fast Startup off' { Set-GcRegistryValue $script:GcFastStartupKey 'HiberbootEnabled' 0 }
    }
}

function Uninstall-GcPower {
    param($Record)
    if ($null -eq $Record) { return }
    foreach ($key in @($Record.Keys)) {
        if ($key -eq 'HiberbootEnabled') {
            Invoke-GcChange 'put Fast Startup back' {
                Restore-GcRegistryValue $script:GcFastStartupKey 'HiberbootEnabled' $Record[$key]
            }
            continue
        }
        $plan, $source, $group, $setting = $key.Split('|')
        Invoke-GcChange "put $setting back to $($Record[$key]) ($source, plan $plan)" {
            Set-GcPowerValue $plan $source $group $setting ([int]$Record[$key])
        }
    }
    Invoke-GcChange 'apply the active plan again' { Invoke-GcNative 'powercfg.exe' @('/setactive', 'SCHEME_CURRENT') | Out-Null }
}
