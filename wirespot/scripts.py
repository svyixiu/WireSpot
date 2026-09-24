"""PowerShell scripts, kept as data. Each reports one JSON object via Emit.

Identity rules used throughout:
  * Adapters are identified by their interface GUID (NetCfgInstanceId).
    Get-NetAdapter.InterfaceGuid, WinRT NetworkAdapter.NetworkAdapterId and
    HNetCfg INetConnectionProps.Guid are the same value; names such as
    "Local Area Connection* 11" are generated, localised, and reused.
  * ifIndex is only used within one inventory snapshot (it changes whenever
    an adapter is re-created, e.g. every WireGuard reconnect).
"""

ICS_ENUM_FN = r'''
function Get-IcsConnections($share) {
    $list = @()
    foreach ($c in @($share.EnumEveryConnection())) {
        $e = [ordered]@{ name=''; guid=''; device=''; status=-1; media=-1; characteristics=0;
                         sharing_enabled=$false; sharing_type=-1; error='' }
        try {
            $p = $share.NetConnectionProps($c)
            $e.name = [string]$p.Name; $e.guid = NGuid $p.Guid; $e.device = [string]$p.DeviceName
            $e.status = [int]$p.Status; $e.media = [int]$p.MediaType; $e.characteristics = [int]$p.Characteristics
        } catch { $e.error = 'props: ' + $_.Exception.Message }
        try {
            $cfg = $share.INetSharingConfigurationForINetConnection($c)
            $e.sharing_enabled = [bool]$cfg.SharingEnabled
            if ($cfg.SharingEnabled) { $e.sharing_type = [int]$cfg.SharingConnectionType }
        } catch { $e.error = ($e.error + ' cfg: ' + $_.Exception.Message).Trim() }
        $list += $e
    }
    return ,$list
}
'''

# Classic ICS flags straight from the HomeNet store HNetCfg is built on.
# Readable without administrator rights.
ICS_FLAGS_FN = r'''
function Get-IcsFlags {
    $names = @{}
    Get-CimInstance -Namespace root/Microsoft/HomeNet -ClassName HNet_Connection -ErrorAction Stop |
        ForEach-Object { $names[(NGuid $_.Guid)] = [string]$_.Name }
    $present = @{}
    Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | ForEach-Object { $present[(NGuid $_.InterfaceGuid)] = $true }
    $list = @()
    Get-CimInstance -Namespace root/Microsoft/HomeNet -ClassName HNet_ConnectionProperties -ErrorAction Stop |
        Where-Object { $_.IsIcsPublic -or $_.IsIcsPrivate } | ForEach-Object {
            $g = NGuid (([string]$_.Connection) -replace '.*Guid = "([^"]+)".*', '$1')
            $list += [ordered]@{ guid=$g; name=$names[$g]; public=[bool]$_.IsIcsPublic; private=[bool]$_.IsIcsPrivate; exists=[bool]$present[$g] }
        }
    return ,$list
}
'''


INVENTORY_PS = r'''
param([switch]$WithIcs)
''' + ICS_ENUM_FN + ICS_FLAGS_FN + r'''
$out = [ordered]@{ ok = $true; errors = @() }
try {
    $script:Stage = 'adapters'
    $out.adapters = @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{ name=[string]$_.Name; description=[string]$_.InterfaceDescription; status=[string]$_.Status;
                    ifindex=[int]$_.ifIndex; guid=(NGuid $_.InterfaceGuid); mac=[string]$_.MacAddress;
                    hardware=[bool]$_.HardwareInterface; virtual=[bool]$_.Virtual; hidden=[bool]$_.Hidden;
                    component=[string]$_.ComponentID; driver_version=[string]$_.DriverVersion;
                    driver_provider=[string]$_.DriverProvider; driver_date=[string]$_.DriverDate;
                    media=[string]$_.NdisPhysicalMedium; link_speed=[string]$_.LinkSpeed }
    })
    $script:Stage = 'ip'
    $out.ipv4 = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{ ifindex=[int]$_.InterfaceIndex; ip=[string]$_.IPAddress; prefix=[int]$_.PrefixLength; origin=[string]$_.PrefixOrigin } })
    $out.ipif = @(Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{ ifindex=[int]$_.InterfaceIndex; forwarding=[string]$_.Forwarding; metric=[int]$_.InterfaceMetric;
                    automatic_metric=[string]$_.AutomaticMetric; state=[string]$_.ConnectionState; dhcp=[string]$_.Dhcp } })
    $out.profiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{ ifindex=[int]$_.InterfaceIndex; name=[string]$_.Name; ipv4=[string]$_.IPv4Connectivity; category=[string]$_.NetworkCategory } })
    $out.routes = @(Get-NetRoute -ErrorAction SilentlyContinue | Where-Object {
            $_.DestinationPrefix -in @('0.0.0.0/0','0.0.0.0/1','128.0.0.0/1','::/0','::/1','8000::/1') } | ForEach-Object {
        [ordered]@{ prefix=[string]$_.DestinationPrefix; ifindex=[int]$_.InterfaceIndex; nexthop=[string]$_.NextHop; metric=[int]$_.RouteMetric } })
    $out.dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.ServerAddresses } | ForEach-Object {
        [ordered]@{ ifindex=[int]$_.InterfaceIndex; servers=@($_.ServerAddresses) } })
    $script:Stage = 'services'
    $out.services = @(Get-Service -Name SharedAccess,icssvc,WlanSvc,WinNat,Dnscache -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{ name=[string]$_.Name; status=[string]$_.Status; start=[string]$_.StartType } })
    $sa = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters' -ErrorAction SilentlyContinue
    $out.ics_scope = if ($sa -and $sa.ScopeAddress) { [string]$sa.ScopeAddress } else { '192.168.137.1' }
    try { $out.ics_flags = Get-IcsFlags } catch { $out.ics_flags_error = $_.Exception.Message }
    if ($WithIcs) {
        $script:Stage = 'ics'
        try {
            $share = New-Object -ComObject HNetCfg.HNetShare
            $out.ics = Get-IcsConnections $share
        } catch { $out.ics = $null; $out.ics_error = $_.Exception.Message }
    }
} catch {
    $out.ok = $false; $out.error = $_.Exception.Message; $out.stage = $script:Stage
}
Emit $out
'''

HOTSPOT_PS = r'''
param(
    [ValidateSet('status','start','stop')][string]$Action = 'status',
    [ValidateSet('vpn','wifi','any')][string]$Source = 'any',
    [string]$TunnelGuid = '',
    [string]$WifiGuid = '',
    [string]$Ssid = '',
    [string]$Band = 'auto',
    [string]$Security = 'wpa2',
    [int]$VerifySeconds = 20
)
$NS = 'Windows.Networking.NetworkOperators'
$TM = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]
$ResultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]
$out = [ordered]@{ ok = $false; action = $Action; build = [Environment]::OSVersion.Version.Build }

function Fail($code, $msg) { $out.ok = $false; $out.code = $code; $out.error = $msg; $out.stage = $script:Stage; Emit $out; exit 0 }
function EnumType($name) {
    try { return [Type]("$NS.$name, $NS, ContentType=WindowsRuntime") } catch { return $null }
}
function EnumValue($name, $member) {
    $t = EnumType $name
    if (-not $t) { return $null }
    if ([Enum]::GetNames($t) -notcontains $member) { return $null }
    return [Enum]::Parse($t, $member)
}

try {
    $script:Stage = 'profiles'
    $cands = @()
    foreach ($p in [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles()) {
        $aid = ''; try { if ($p.NetworkAdapter) { $aid = NGuid $p.NetworkAdapter.NetworkAdapterId } } catch {}
        $lvl = 'None'; try { $lvl = $p.GetNetworkConnectivityLevel().ToString() } catch {}
        $cap = ''; try { $cap = $TM::GetTetheringCapabilityFromConnectionProfile($p).ToString() } catch { $cap = 'Error: ' + $_.Exception.Message }
        $kind = if ($TunnelGuid -and $aid -eq (NGuid $TunnelGuid)) { 'vpn' } elseif ($p.IsWlanConnectionProfile) { 'wifi' } else { 'other' }
        $cands += [pscustomobject]@{ p=$p; name=[string]$p.ProfileName; adapter=$aid; level=$lvl; capability=$cap; kind=$kind; wlan=[bool]$p.IsWlanConnectionProfile }
    }
    $out.profiles = @($cands | Where-Object { $_.level -ne 'None' } | ForEach-Object {
        [ordered]@{ name=$_.name; adapter=$_.adapter; level=$_.level; capability=$_.capability; kind=$_.kind } })

    function Pick($kind) {
        $live = @($cands | Where-Object { $_.level -ne 'None' })
        if ($kind -eq 'vpn') { return $live | Where-Object { $_.kind -eq 'vpn' } | Select-Object -First 1 }
        $w = @($live | Where-Object { $_.kind -eq 'wifi' })
        if ($WifiGuid) { $w = @($w | Where-Object { $_.adapter -eq (NGuid $WifiGuid) }) }
        return $w | Sort-Object @{ Expression = { if ($_.level -eq 'InternetAccess') { 0 } else { 1 } } } | Select-Object -First 1
    }
    $order = switch ($Source) { 'vpn' { @('vpn') } 'wifi' { @('wifi') } default { @('vpn','wifi') } }
    $chosen = $null
    foreach ($k in $order) { $c = Pick $k; if ($c) { $chosen = $c; break } }
    if (-not $chosen) {
        if ($Action -eq 'start') { Fail 'source_profile_missing' "No connected '$Source' connection profile is available as the hotspot source." }
        # For status/stop fall back to any live profile: the hotspot session is global.
        $chosen = $cands | Where-Object { $_.level -ne 'None' } | Select-Object -First 1
        if (-not $chosen) { Fail 'no_profiles' 'Windows reports no connected network profile.' }
    }
    $out.source = [ordered]@{ name=$chosen.name; adapter=$chosen.adapter; kind=$chosen.kind; capability=$chosen.capability; level=$chosen.level }

    $script:Stage = 'manager'
    $mgr = $TM::CreateFromConnectionProfile($chosen.p)
    $out.state = $mgr.TetheringOperationalState.ToString()
    $out.clients = [int]$mgr.ClientCount
    $out.max_clients = [int]$mgr.MaxClientCount

    if ($Action -eq 'status') {
        $script:Stage = 'status'
        try {
            $cur = $mgr.GetCurrentAccessPointConfiguration()
            $out.ap = [ordered]@{ ssid = [string]$cur.Ssid }
            if ($cur | Get-Member -Name Band) { $out.ap.band = $cur.Band.ToString() }
            if ($cur | Get-Member -Name AuthenticationKind) { $out.ap.auth = $cur.AuthenticationKind.ToString() }
        } catch { $out.ap_error = $_.Exception.Message }
        $probe = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration]::new()
        $bands = [ordered]@{}
        $bt = EnumType 'TetheringWiFiBand'
        if ($bt -and ($probe | Get-Member -Name IsBandSupported)) {
            foreach ($n in [Enum]::GetNames($bt)) {
                if ($n -eq 'Auto') { continue }   # IsBandSupported(Auto) returns E_FAIL on some builds
                try { $bands[$n] = [bool]$probe.IsBandSupported([Enum]::Parse($bt, $n)) } catch { $bands[$n] = 'error: ' + $_.Exception.Message }
            }
        }
        $out.bands = $bands
        $auths = [ordered]@{}
        $at = EnumType 'TetheringWiFiAuthenticationKind'
        if ($at -and ($probe | Get-Member -Name IsAuthenticationKindSupported)) {
            foreach ($n in [Enum]::GetNames($at)) {
                try { $auths[$n] = [bool]$probe.IsAuthenticationKindSupported([Enum]::Parse($at, $n)) } catch { $auths[$n] = 'error: ' + $_.Exception.Message }
            }
        }
        $out.auth_kinds = $auths
        $out.client_list = @()
        try {
            foreach ($cl in $mgr.GetTetheringClients()) {
                $names = @(); foreach ($h in $cl.HostNames) { $names += [string]$h.DisplayName }
                $out.client_list += [ordered]@{ mac=[string]$cl.MacAddress; hostnames=$names }
            }
        } catch { $out.clients_error = $_.Exception.Message }
        try { $out.no_connections_timeout = [bool]$mgr.IsNoConnectionsTimeoutEnabled() } catch {}
        $out.ok = $true
        Emit $out; exit 0
    }

    if ($Action -eq 'stop') {
        $script:Stage = 'stop'
        if ($out.state -eq 'Off') { $out.ok = $true; $out.already_off = $true; Emit $out; exit 0 }
        $r = Await-Result ($mgr.StopTetheringAsync()) $ResultType
        $out.status = $r.Status.ToString(); $out.status_code = [int]$r.Status; $out.message = [string]$r.AdditionalErrorMessage
        for ($i = 0; $i -lt ($VerifySeconds * 2); $i++) { if ($mgr.TetheringOperationalState.ToString() -eq 'Off') { break }; Start-Sleep -Milliseconds 500 }
        $out.state_after = $mgr.TetheringOperationalState.ToString()
        $out.ok = ($out.state_after -eq 'Off')
        Emit $out; exit 0
    }

    # ---- start ----
    $script:Stage = 'capability'
    if ($chosen.capability -ne 'Enabled') { Fail 'capability' "Tethering capability for '$($chosen.name)' is $($chosen.capability)." }

    $script:Stage = 'stop-existing'
    $out.state_before = $out.state
    if ($out.state -ne 'Off') {
        $r0 = Await-Result ($mgr.StopTetheringAsync()) $ResultType
        $out.stop_existing = $r0.Status.ToString()
        if ($r0.Status.ToString() -ne 'Success') { Fail 'stop_existing' "Could not stop the running hotspot before reconfiguring: $($r0.Status) $($r0.AdditionalErrorMessage)" }
        for ($i = 0; $i -lt 20; $i++) { if ($mgr.TetheringOperationalState.ToString() -eq 'Off') { break }; Start-Sleep -Milliseconds 500 }
    }

    $script:Stage = 'configure'
    $pass = $env:WIRESPOT_PASSPHRASE
    if ([string]::IsNullOrEmpty($pass)) { Fail 'no_passphrase' 'Hotspot passphrase was not provided.' }
    # GetCurrentAccessPointConfiguration() can return E_FAIL on some systems; a
    # fresh activatable configuration object avoids it.
    $cfg = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration]::new()
    $cfg.Ssid = $Ssid
    $cfg.Passphrase = $pass
    $out.requested_band = $Band
    $bandName = @{ 'auto'='Auto'; '2.4'='TwoPointFourGigahertz'; '5'='FiveGigahertz'; '6'='SixGigahertz' }[$Band]
    if ($cfg | Get-Member -Name Band) {
        $bv = EnumValue 'TetheringWiFiBand' $bandName
        if ($null -eq $bv) { Fail 'band_unknown_to_os' "This Windows build does not know hotspot band '$Band'." }
        if ($Band -ne 'auto') {
            try {
                $sup = [bool]$cfg.IsBandSupported($bv)
                $out.band_check = $sup
                if (-not $sup) { Fail 'band_unsupported' "The Wi-Fi adapter reports that a $Band GHz hotspot is not supported." }
            } catch { $out.band_check = 'error: ' + $_.Exception.Message }
        }
        $cfg.Band = $bv
    } elseif ($Band -ne 'auto') {
        Fail 'band_unsupported_os' 'Hotspot band selection requires Windows 10 2004 (build 19041) or newer.'
    }
    $authName = @{ 'wpa2'='Wpa2'; 'transition'='Wpa3TransitionMode'; 'wpa3'='Wpa3' }[$Security]
    if ($cfg | Get-Member -Name AuthenticationKind) {
        $av = EnumValue 'TetheringWiFiAuthenticationKind' $authName
        if ($null -eq $av) { Fail 'security_unknown_to_os' "This Windows build does not know hotspot security '$Security'." }
        if ($Security -ne 'wpa2' -and ($cfg | Get-Member -Name IsAuthenticationKindSupported)) {
            try {
                if (-not [bool]$cfg.IsAuthenticationKindSupported($av)) { Fail 'security_unsupported' "The Wi-Fi adapter does not support '$Security' for the hotspot." }
            } catch { $out.security_check = 'error: ' + $_.Exception.Message }
        }
        $cfg.AuthenticationKind = $av
    } elseif ($Security -ne 'wpa2') {
        Fail 'security_unsupported_os' 'WPA3 hotspot security requires Windows 11 24H2 (build 26100) or newer.'
    }
    Await-Action ($mgr.ConfigureAccessPointAsync($cfg))
    $out.configured = $true

    $script:Stage = 'start'
    $r = Await-Result ($mgr.StartTetheringAsync()) $ResultType 60000
    $out.status = $r.Status.ToString()
    $out.status_code = [int]$r.Status
    $out.message = [string]$r.AdditionalErrorMessage

    $script:Stage = 'verify'
    $st = $mgr.TetheringOperationalState.ToString()
    if ($out.status -eq 'Success') {
        for ($i = 0; $i -lt ($VerifySeconds * 2); $i++) {
            $st = $mgr.TetheringOperationalState.ToString()
            if ($st -eq 'On') { break }
            Start-Sleep -Milliseconds 500
        }
    }
    $out.state_after = $st
    try { $out.applied_band = $mgr.GetCurrentAccessPointConfiguration().Band.ToString() } catch {}
    $out.ok = ($out.status -eq 'Success' -and $st -eq 'On')
    if (-not $out.ok) { $out.code = if ($out.status -ne 'Success') { 'start_status' } else { 'not_on' } }
} catch {
    $out.ok = $false; $out.code = 'exception'; $out.error = $_.Exception.Message; $out.stage = $script:Stage
}
Emit $out
'''

ICS_PS = r'''
param(
    [ValidateSet('list','disable')][string]$Action = 'list',
    [string]$DisableGuids = ''
)
''' + ICS_ENUM_FN + r'''
$out = [ordered]@{ ok = $false; action = $Action }
try {
    $script:Stage = 'com'
    $share = New-Object -ComObject HNetCfg.HNetShare
    $out.before = Get-IcsConnections $share
    if ($Action -eq 'disable') {
        $script:Stage = 'disable'
        $out.changes = @()
        $want = @($DisableGuids -split ',' | Where-Object { $_ } | ForEach-Object { NGuid $_ })
        foreach ($c in @($share.EnumEveryConnection())) {
            $g = NGuid $share.NetConnectionProps($c).Guid
            if ($want -notcontains $g) { continue }
            $cfg = $share.INetSharingConfigurationForINetConnection($c)
            if ($cfg.SharingEnabled) { $cfg.DisableSharing(); $out.changes += $g }
        }
    }
    $out.ok = $true
} catch { $out.error = $_.Exception.Message; $out.stage = $script:Stage }
Emit $out
'''

ICS_FLAGS_PS = ICS_FLAGS_FN + r'''
$out = [ordered]@{ ok = $false }
try { $out.flags = Get-IcsFlags; $out.ok = $true } catch { $out.error = $_.Exception.Message }
Emit $out
'''

SERVICES_PS = r'''
$out = [ordered]@{ ok = $true }
$out.services = @(Get-CimInstance Win32_Service -Filter "Name LIKE 'WireGuardTunnel%'" -ErrorAction SilentlyContinue | ForEach-Object {
    [ordered]@{ name=[string]$_.Name; state=[string]$_.State; start_mode=[string]$_.StartMode; path=[string]$_.PathName } })
$out.manager = @(Get-Service -Name WireGuardManager -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.Status })
Emit $out
'''

DNS_LOCK_PS = r'''
param([ValidateSet('add','remove','list')][string]$Action = 'list', [string]$Servers = '', [string]$Tag = 'WireSpot-owned')
$out = [ordered]@{ ok = $false; action = $Action }
try {
    if ($Action -eq 'add') {
        $script:Stage = 'add'
        Get-DnsClientNrptRule -ErrorAction SilentlyContinue | Where-Object { $_.Comment -eq $Tag } | ForEach-Object { Remove-DnsClientNrptRule -Name $_.Name -Force }
        $ns = @($Servers -split ',' | Where-Object { $_ })
        $r = Add-DnsClientNrptRule -Namespace '.' -NameServers $ns -Comment $Tag -PassThru
        $out.rule = [string]$r.Name
        Clear-DnsClientCache -ErrorAction SilentlyContinue
    } elseif ($Action -eq 'remove') {
        $script:Stage = 'remove'
        $removed = @()
        Get-DnsClientNrptRule -ErrorAction SilentlyContinue | Where-Object { $_.Comment -eq $Tag } | ForEach-Object {
            Remove-DnsClientNrptRule -Name $_.Name -Force; $removed += [string]$_.Name }
        $out.removed = $removed
        Clear-DnsClientCache -ErrorAction SilentlyContinue
    }
    $out.rules = @(Get-DnsClientNrptRule -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{ name=[string]$_.Name; namespace=@($_.Namespace); servers=@($_.NameServers); comment=[string]$_.Comment; ours=($_.Comment -eq $Tag) } })
    try {
        $out.effective = @(Get-DnsClientNrptPolicy -Effective -ErrorAction Stop | ForEach-Object {
            [ordered]@{ namespace=[string]$_.Namespace; servers=@($_.NameServers) } })
    } catch { $out.effective_error = $_.Exception.Message }
    $out.ok = $true
} catch { $out.error = $_.Exception.Message; $out.stage = $script:Stage }
Emit $out
'''

ROUTE_CHECK_PS = r'''
param([string]$Targets = '1.1.1.1,9.9.9.9')
$out = [ordered]@{ ok = $true; routes = @() }
foreach ($t in ($Targets -split ',' | Where-Object { $_ })) {
    try {
        $r = Find-NetRoute -RemoteIPAddress $t -ErrorAction Stop | Where-Object { $_.DestinationPrefix } | Select-Object -First 1
        $out.routes += [ordered]@{ target=$t; ifindex=[int]$r.InterfaceIndex; alias=[string]$r.InterfaceAlias; prefix=[string]$r.DestinationPrefix; nexthop=[string]$r.NextHop }
    } catch { $out.routes += [ordered]@{ target=$t; error=$_.Exception.Message } }
}
Emit $out
'''

CLIENTS_PS = r'''
param([string]$HotspotGuid = '', [switch]$Resolve)
$out = [ordered]@{ ok = $false; tethering = @(); neighbors = @() }
try {
    $script:Stage = 'tethering'
    try {
        $prof = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles() |
            Where-Object { $_.GetNetworkConnectivityLevel().ToString() -ne 'None' } | Select-Object -First 1
        if ($prof) {
            $mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($prof)
            $out.state = $mgr.TetheringOperationalState.ToString()
            foreach ($cl in $mgr.GetTetheringClients()) {
                $hn = @()
                foreach ($h in $cl.HostNames) { $hn += [ordered]@{ name=[string]$h.DisplayName; type=[string]$h.Type } }
                $out.tethering += [ordered]@{ mac=[string]$cl.MacAddress; hostnames=$hn }
            }
        }
    } catch { $out.tethering_error = $_.Exception.Message }

    $script:Stage = 'neighbors'
    if ($HotspotGuid) {
        $ad = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object { (NGuid $_.InterfaceGuid) -eq (NGuid $HotspotGuid) } | Select-Object -First 1
        if ($ad) {
            $out.ifindex = [int]$ad.ifIndex
            $out.neighbors = @(Get-NetNeighbor -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.State -in @('Reachable','Stale','Delay','Probe','Permanent') -and $_.LinkLayerAddress -and
                               $_.LinkLayerAddress -notmatch '^(00-00-00-00-00-00|FF-FF-FF-FF-FF-FF|01-00-5E)' } |
                ForEach-Object { [ordered]@{ ip=[string]$_.IPAddress; mac=[string]$_.LinkLayerAddress; state=[string]$_.State } })
        }
    }
    if ($Resolve) {
        $script:Stage = 'resolve'
        foreach ($n in $out.neighbors) {
            try {
                $task = [System.Net.Dns]::GetHostEntryAsync($n.ip)
                if ($task.Wait(1200) -and $task.Result.HostName -ne $n.ip) { $n.ptr = [string]$task.Result.HostName }
            } catch {}
        }
    }
    $out.ok = $true
} catch { $out.error = $_.Exception.Message; $out.stage = $script:Stage }
Emit $out
'''

ADAPTER_PS = r'''
param([string]$Name = '', [string]$Guid = '')
$out = [ordered]@{ ok = $true; adapter = $null }
$a = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object {
    ($Name -and $_.Name -eq $Name) -or ($Guid -and (NGuid $_.InterfaceGuid) -eq (NGuid $Guid)) } | Select-Object -First 1
if ($a) {
    $ips = @(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.IPAddress })
    $dns = @((Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses)
    $out.adapter = [ordered]@{ name=[string]$a.Name; description=[string]$a.InterfaceDescription; status=[string]$a.Status;
                               ifindex=[int]$a.ifIndex; guid=(NGuid $a.InterfaceGuid); ipv4=$ips; dns=$dns }
}
Emit $out
'''


# Windows turns Mobile Hotspot off after ~5 minutes without clients
# ("power saving"). WireSpot disables that while it runs and restores it on stop.
HOTSPOT_TIMEOUT_PS = r'''
param([switch]$Enable)
$out = [ordered]@{ ok = $false }
try {
    $prof = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles() |
        Where-Object { $_.GetNetworkConnectivityLevel().ToString() -ne 'None' } | Select-Object -First 1
    $mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($prof)
    $out.before = [bool]$mgr.IsNoConnectionsTimeoutEnabled()
    if ($Enable) { $mgr.EnableNoConnectionsTimeout() } else { $mgr.DisableNoConnectionsTimeout() }
    $out.after = [bool]$mgr.IsNoConnectionsTimeoutEnabled()
    $out.ok = $true
} catch { $out.error = $_.Exception.Message }
Emit $out
'''

# One process per guard tick: tunnel service, hotspot state, ICS bindings, clients.
GUARD_PS = r'''
param([string]$Tunnel = '', [string]$HotspotGuid = '')
''' + ICS_FLAGS_FN + r'''
$out = [ordered]@{ ok = $true }
try { $out.tunnel_state = [string](Get-Service -Name ('WireGuardTunnel$' + $Tunnel) -ErrorAction Stop).Status } catch { $out.tunnel_state = 'Missing' }
try {
    $prof = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles() |
        Where-Object { $_.GetNetworkConnectivityLevel().ToString() -ne 'None' } | Select-Object -First 1
    if ($prof) {
        $mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($prof)
        $out.hotspot_state = $mgr.TetheringOperationalState.ToString()
        $out.tethering = @()
        foreach ($cl in $mgr.GetTetheringClients()) {
            $hn = @(); foreach ($h in $cl.HostNames) { $hn += [ordered]@{ name=[string]$h.DisplayName; type=[string]$h.Type } }
            $out.tethering += [ordered]@{ mac=[string]$cl.MacAddress; hostnames=$hn }
        }
    } else { $out.hotspot_state = 'NoProfile' }
} catch { $out.hotspot_error = $_.Exception.Message }
try { $out.flags = Get-IcsFlags } catch { $out.flags_error = $_.Exception.Message }
if ($HotspotGuid) {
    $ad = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object { (NGuid $_.InterfaceGuid) -eq (NGuid $HotspotGuid) } | Select-Object -First 1
    if ($ad) {
        $out.neighbors = @(Get-NetNeighbor -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.State -in @('Reachable','Stale','Delay','Probe') -and $_.LinkLayerAddress -and
                           $_.LinkLayerAddress -notmatch '^(00-00-00-00-00-00|FF-FF-FF-FF-FF-FF|01-00-5E)' } |
            ForEach-Object { [ordered]@{ ip=[string]$_.IPAddress; mac=[string]$_.LinkLayerAddress; state=[string]$_.State } })
    }
}
Emit $out
'''

ACL_PS = r'''
param([string]$Paths = '')
$out = [ordered]@{ ok = $true; items = @() }
foreach ($p in ($Paths -split '\|' | Where-Object { $_ })) {
    $e = [ordered]@{ path = $p; exists = (Test-Path -LiteralPath $p); aces = @() }
    if ($e.exists) {
        try {
            foreach ($a in (Get-Acl -LiteralPath $p).Access) {
                $sid = ''; try { $sid = $a.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { $sid = [string]$a.IdentityReference }
                $e.aces += [ordered]@{ sid=$sid; who=[string]$a.IdentityReference; rights=[string]$a.FileSystemRights; type=[string]$a.AccessControlType }
            }
        } catch { $e.error = $_.Exception.Message }
        if ((Get-Item -LiteralPath $p) -is [System.IO.DirectoryInfo]) { $e.files = @(Get-ChildItem -LiteralPath $p -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }) }
    }
    $out.items += $e
}
Emit $out
'''

SYSINFO_PS = r'''
param([string]$Files = '')
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction SilentlyContinue
$out = [ordered]@{ ok = $true; product = [string]$cv.ProductName; display = [string]$cv.DisplayVersion; build = [string]$cv.CurrentBuild;
                   ubr = [string]$cv.UBR; edition = [string]$cv.EditionID; ps = $PSVersionTable.PSVersion.ToString(); files = @() }
foreach ($f in ($Files -split '\|' | Where-Object { $_ })) {
    if (Test-Path -LiteralPath $f) { $out.files += [ordered]@{ path=$f; version=[string](Get-Item -LiteralPath $f).VersionInfo.ProductVersion } }
}
Emit $out
'''


# The real internet uplink = the non-tunnel interface holding the best IPv4
# default route (Wi-Fi, Ethernet, USB tethering...).
UPLINK_PS = r"""
$out = [ordered]@{ ok = $true; uplinks = @() }
$tunnels = @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceDescription -like 'WireGuard*' -or $_.InterfaceDescription -like '*Wintun*' } |
    ForEach-Object { [int]$_.ifIndex })
$routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Where-Object { $tunnels -notcontains [int]$_.InterfaceIndex })
$ranked = foreach ($r in $routes) {
    $ifm = (Get-NetIPInterface -InterfaceIndex $r.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).InterfaceMetric
    [pscustomobject]@{ r = $r; m = [int]$r.RouteMetric + [int]$ifm }
}
foreach ($x in @($ranked | Sort-Object m)) {
    $a = Get-NetAdapter -InterfaceIndex $x.r.InterfaceIndex -ErrorAction SilentlyContinue
    if ($a -and $a.Status -eq 'Up') {
        $out.uplinks += [ordered]@{ name=[string]$a.Name; description=[string]$a.InterfaceDescription;
            media=[string]$a.NdisPhysicalMedium; ifindex=[int]$a.ifIndex; gateway=[string]$x.r.NextHop;
            hardware=[bool]$a.HardwareInterface; guid=(NGuid $a.InterfaceGuid); metric=$x.m }
    }
}
Emit $out
"""
