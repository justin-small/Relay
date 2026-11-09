<#
Live Caption Relay on Windows: announces Relay on the LAN so caption panels find it
(DNS-SD service _relay-captions._tcp, the viewer port, a TXT record with the Relay
version and install name).

  announce.ps1 start -Ip 192.168.1.20   register, in a hidden process of its own (start.bat)
  announce.ps1 stop                     withdraw the announcement (stop.bat)

Uses Windows' own DNS-SD API (dnsapi.dll, Windows 10 1809 and later), so no Bonjour
install is needed. Docker Desktop's container is not on the LAN, so this runs on the
host, next to Docker. RELAY_ANNOUNCE=0 turns it off; RELAY_INSTALL_NAME names this
install (default: the computer's name).

The registration lives as long as the process that made it. `start` launches that
process hidden; `stop` asks it to deregister by creating a stop file, and ends it if it
does not.

Written for Windows PowerShell 5.1, which is what every Windows 10 has.
#>
param(
    [Parameter(Mandatory, Position = 0)][ValidateSet('start', 'stop', 'run')][string]$Action,
    [string]$Ip
)

$ErrorActionPreference = 'Stop'
$Pidfile = Join-Path $env:TEMP 'relay-announce.pid'
$StopFile = Join-Path $env:TEMP 'relay-announce.stop'
$Log = Join-Path $env:TEMP 'relay-announce.log'
$Service = '_relay-captions._tcp.local'

$Name = if ($env:RELAY_INSTALL_NAME) { $env:RELAY_INSTALL_NAME } else { $env:COMPUTERNAME }
$Port = if ($env:RELAY_HTTP_PORT) { [int]$env:RELAY_HTTP_PORT } else { 80 }
$Version = if ($env:RELAY_VERSION) { $env:RELAY_VERSION } else {
    $v = $null
    try { $v = (& git describe --tags --always 2>$null) } catch { }
    if ($v) { $v } else { 'dev' }
}

function Add-Dnssd {
    if ('Relay.Dnssd' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace Relay {
public static class Dnssd {
    public delegate void Complete(uint status, IntPtr context, IntPtr instance);
    [StructLayout(LayoutKind.Sequential)]
    struct Req {
        public uint Version; public uint InterfaceIndex; public IntPtr Instance;
        public IntPtr Callback; public IntPtr Context; public IntPtr Credentials; public int Unicast;
    }
    [DllImport("dnsapi.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr DnsServiceConstructInstance(string name, string host, IntPtr ip4, IntPtr ip6,
        ushort port, ushort priority, ushort weight, uint count, string[] keys, string[] values);
    [DllImport("dnsapi.dll")] static extern uint DnsServiceRegister(ref Req r, IntPtr cancel);
    [DllImport("dnsapi.dll")] static extern uint DnsServiceDeRegister(ref Req r, IntPtr cancel);
    static readonly Complete Callback = delegate(uint s, IntPtr c, IntPtr i) { };
    static Req req;
    static IntPtr ip4Mem = IntPtr.Zero;
    const uint Pending = 9506;  // DNS_REQUEST_PENDING: registration continues in the background

    public static uint Register(string instance, string host, string ip, ushort port, string[] keys, string[] values) {
        byte[] b = System.Net.IPAddress.Parse(ip).GetAddressBytes();
        ip4Mem = Marshal.AllocHGlobal(4);
        Marshal.Copy(b, 0, ip4Mem, 4);   // IP4_ADDRESS is the four bytes in network order
        IntPtr inst = DnsServiceConstructInstance(instance, host, ip4Mem, IntPtr.Zero, port, 0, 0,
            (uint)keys.Length, keys, values);
        if (inst == IntPtr.Zero) return 1;
        req = new Req();
        req.Version = 1; req.InterfaceIndex = 0; req.Instance = inst;
        req.Callback = Marshal.GetFunctionPointerForDelegate(Callback);
        uint r = DnsServiceRegister(ref req, IntPtr.Zero);
        return r == Pending ? 0 : r;
    }
    public static uint Deregister() {
        uint r = DnsServiceDeRegister(ref req, IntPtr.Zero);
        return r == Pending ? 0 : r;
    }
}
}
'@
}

switch ($Action) {
    'start' {
        if (-not $Ip) { Write-Host '  announce: no LAN address given; not announcing.'; exit 0 }
        # A previous one still running is replaced, so there is only ever one.
        & $PSCommandPath stop | Out-Null
        Remove-Item $StopFile -Force -ErrorAction SilentlyContinue
        $p = Start-Process -FilePath powershell.exe -WindowStyle Hidden -PassThru `
            -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", 'run', '-Ip', $Ip
        Set-Content -Path $Pidfile -Value $p.Id -Encoding ASCII
        Start-Sleep 2
        if ($p.HasExited) {
            Write-Host "  announce: could not register with Windows. Log: $Log"
            Get-Content $Log -Tail 3 -ErrorAction SilentlyContinue | ForEach-Object { "    $_" }
            Write-Host '  Panels can still be given the address by hand on their setup page.'
            exit 0
        }
        Write-Host "  Announced   : `"$Name`" ($Service) for caption panels"
    }

    'run' {
        try {
            Add-Dnssd
            $hostName = ($env:COMPUTERNAME + '.local')
            $rc = [Relay.Dnssd]::Register("$Name.$Service", $hostName, $Ip, [uint16]$Port,
                [string[]]@('v', 'name', 'path'), [string[]]@($Version, $Name, '/'))
            if ($rc -ne 0) { throw "DnsServiceRegister returned $rc" }
            "$(Get-Date -Format s) announced $Name at ${Ip}:$Port (v=$Version)" | Set-Content $Log
            while (-not (Test-Path $StopFile)) { Start-Sleep 1 }
            [void][Relay.Dnssd]::Deregister()
        } catch {
            "$(Get-Date -Format s) $($_.Exception.Message)" | Set-Content $Log
            exit 1
        }
    }

    'stop' {
        if (Test-Path $Pidfile) {
            $id = [int](Get-Content $Pidfile -ErrorAction SilentlyContinue)
            $p = Get-Process -Id $id -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -eq 'powershell') {
                New-Item -ItemType File -Path $StopFile -Force | Out-Null
                if (-not $p.WaitForExit(4000)) { $p | Stop-Process -Force }
                Write-Host 'Stopped the panel announcement.'
            }
            Remove-Item $Pidfile, $StopFile -Force -ErrorAction SilentlyContinue
        }
    }
}
exit 0
