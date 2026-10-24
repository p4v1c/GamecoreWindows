# Automatic sign-in of the console user, so the box boots into GameCore. The
# password is asked at the console, with the owner there, and stored as the
# LSA secret Winlogon reads (as Sysinternals Autologon does), never in the
# registry in clear and never in install.conf. Skipped with AUTOLOGON=0.

$script:GcWinlogonKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
# "Only allow Windows Hello sign-in" hides password sign-in, and autologon with it.
$script:GcPasswordLessKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'

$script:GcLsaSource = @'
using System;
using System.Runtime.InteropServices;
public static class GcLsaSecret {
    [StructLayout(LayoutKind.Sequential)] struct LSA_UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
    [StructLayout(LayoutKind.Sequential)] struct LSA_OBJECT_ATTRIBUTES { public int Length; public IntPtr RootDirectory; public IntPtr ObjectName; public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService; }
    [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr system, ref LSA_OBJECT_ATTRIBUTES attributes, uint access, out IntPtr policy);
    [DllImport("advapi32.dll")] static extern uint LsaStorePrivateData(IntPtr policy, ref LSA_UNICODE_STRING key, IntPtr data);
    [DllImport("advapi32.dll")] static extern uint LsaStorePrivateData(IntPtr policy, ref LSA_UNICODE_STRING key, ref LSA_UNICODE_STRING data);
    [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr policy);
    [DllImport("advapi32.dll")] static extern int LsaNtStatusToWinError(uint status);
    const uint POLICY_CREATE_SECRET = 0x20;
    static LSA_UNICODE_STRING Text(string value) {
        var s = new LSA_UNICODE_STRING();
        s.Buffer = Marshal.StringToHGlobalUni(value);
        s.Length = (ushort)(value.Length * 2);
        s.MaximumLength = (ushort)(value.Length * 2 + 2);
        return s;
    }
    /// <summary>Store (value) or delete (null) the secret named key.</summary>
    public static void Store(string key, string value) {
        var attributes = new LSA_OBJECT_ATTRIBUTES();
        IntPtr policy;
        uint status = LsaOpenPolicy(IntPtr.Zero, ref attributes, POLICY_CREATE_SECRET, out policy);
        if (status != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(status));
        var name = Text(key);
        var data = value == null ? new LSA_UNICODE_STRING() : Text(value);
        try {
            status = value == null ? LsaStorePrivateData(policy, ref name, IntPtr.Zero) : LsaStorePrivateData(policy, ref name, ref data);
            if (status != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(status));
        } finally {
            LsaClose(policy);
            Marshal.FreeHGlobal(name.Buffer);
            if (data.Buffer != IntPtr.Zero) { Marshal.ZeroFreeGlobalAllocUnicode(data.Buffer); }
        }
    }
}
'@

function Set-GcLsaSecret {
    param([Parameter(Mandatory)][string]$Name, $Value)  # untyped: [string] turns $null into ""
    if (-not ('GcLsaSecret' -as [type])) { Add-Type -TypeDefinition $script:GcLsaSource -Language CSharp }
    [GcLsaSecret]::Store($Name, $Value)
}

function Read-GcLogonPassword {
    param([Parameter(Mandatory)][string]$User)
    $secure = Read-Host -AsSecureString "   Windows password of $User (for automatic sign-in; empty if the account has none)"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Test-GcAutologonSet {
    param([Parameter(Mandatory)][string]$User)
    return (Get-GcRegistryValue $script:GcWinlogonKey 'AutoAdminLogon') -eq '1' -and
        (Get-GcRegistryValue $script:GcWinlogonKey 'DefaultUserName') -ieq $User
}

function Install-GcAutologon {
    param([Parameter(Mandatory)]$Settings, [switch]$Unattended)
    if (-not $Settings.Autologon) { Write-GcInfo 'AUTOLOGON=0: skipped'; return }
    $user = $Settings.ConsoleUser
    if (Test-GcAutologonSet $user) { Write-GcInfo "$user already signs in automatically"; return }
    if ($Unattended) {
        Write-GcWarning ("automatic sign-in needs the account's password, typed by the owner: run " +
            '.\install\install.ps1 -Only configure-autologon at the PC')
        return
    }
    foreach ($name in 'AutoAdminLogon', 'DefaultUserName', 'DefaultDomainName') {
        Set-GcRecord 'configure-autologon' $name (Get-GcRegistryValue $script:GcWinlogonKey $name)
    }
    Set-GcRecord 'configure-autologon' 'DevicePasswordLessBuildVersion' (Get-GcRegistryValue $script:GcPasswordLessKey 'DevicePasswordLessBuildVersion')
    Invoke-GcChange "sign $user in automatically at boot" {
        $password = Read-GcLogonPassword $user
        Set-GcLsaSecret 'DefaultPassword' $password
        Remove-ItemProperty -LiteralPath $script:GcWinlogonKey -Name 'DefaultPassword' -ErrorAction SilentlyContinue
        Set-GcRegistryValue $script:GcWinlogonKey 'DefaultUserName' $user 'String'
        Set-GcRegistryValue $script:GcWinlogonKey 'DefaultDomainName' $env:COMPUTERNAME 'String'
        Set-GcRegistryValue $script:GcWinlogonKey 'AutoAdminLogon' '1' 'String'
        Set-GcRegistryValue $script:GcPasswordLessKey 'DevicePasswordLessBuildVersion' 0
    }
}

function Uninstall-GcAutologon {
    param($Record)
    if ($null -eq $Record) { return }
    Invoke-GcChange 'stop signing in automatically' {
        Set-GcLsaSecret 'DefaultPassword' $null
        foreach ($name in 'AutoAdminLogon', 'DefaultUserName', 'DefaultDomainName') {
            Restore-GcRegistryValue $script:GcWinlogonKey $name $Record[$name] 'String'
        }
        Restore-GcRegistryValue $script:GcPasswordLessKey 'DevicePasswordLessBuildVersion' $Record['DevicePasswordLessBuildVersion']
    }
}
