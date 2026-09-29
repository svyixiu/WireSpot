import type { Device, Hello, ModelEntry, Snap } from '@/composables/engine'

/**
 * A pretend WireSpot engine for previewing the interface in a browser
 * (`pnpm dev`, outside the desktop app). It answers the same calls and pushes
 * the same events as wirespot/bridge.py, with made-up data. Nothing here
 * touches the network.
 */

type Emit = (event: string, data: any) => void

const PROFILES = [
    { name: 'NL-FREE-12.conf', stem: 'NL-FREE-12', label: 'NL-FREE#12 · Netherlands', server: 'NL-FREE#12', country: 'Netherlands', country_code: 'NL', entry_country_code: '', free: true, endpoint: '185.107.56.12:51820', full_tunnel: true, allowed_ips: ['0.0.0.0/0'], features: ['NetShield'] },
    { name: 'CH-US-3.conf', stem: 'CH-US-3', label: 'CH-US#3 · United States', server: 'CH-US#3', country: 'United States', country_code: 'US', entry_country_code: 'CH', free: false, endpoint: '185.159.157.9:51820', full_tunnel: true, allowed_ips: ['0.0.0.0/0'], features: ['Secure Core', 'NetShield'] },
]

export function createMockEngine(emit: Emit) {
    const s = {
        state: 'idle' as 'idle' | 'live' | 'vpn' | 'paused' | 'error',
        busy: null as string | null,
        profile: PROFILES[0].name,
        readySince: 0,
        pausedUntil: 0,
        rx: 0,
        tx: 0,
        autostart: true,
        settings: {
            hotspot: { ssid: 'WireSpot-4F2A', password: 'correct-horse-battery', band: 'auto', security: 'wpa2' },
            vpn: { protection: 'balanced', default_profile: PROFILES[0].name },
            behavior: { approve_devices: true, autoconnect: false, guard: true, dns_lock: true, watch_downloads: true, debug: false, profile_less: false } as Record<string, boolean>,
        },
        devices: [] as Device[],
        blocked: [] as Device[],
        remembered: [{ mac: 'a4:83:e7:10:22:9c', name: "Maya's iPad" }],
        speed: null as any,
        report: { section: '', running: false, lines: [] as { level: string; text: string }[] },
        journal: [] as { ts: number; source: string; level: string; text: string }[],
        checks: {} as Record<string, any>,
        timers: [] as ReturnType<typeof setTimeout>[],
        asked: false,
    }

    // ?update=demo pretends a newer version is out (trying the update dialog in a browser)
    const fakeUpdate = new URLSearchParams(location.search).get('update') === 'demo'
    let updateCancelled = false

    const later = (ms: number, fn: () => void) => { s.timers.push(setTimeout(fn, ms)) }
    const log = (text: string, level = 'ok', source = 'app') => {
        s.journal.push({ ts: Date.now() / 1000, source, level, text })
        if (s.journal.length > 500) s.journal.shift()
    }
    const toast = (title: string, body: string, error = false) => emit('toast', { title, body, error })
    const prof = () => PROFILES.find(p => p.name === s.profile) ?? PROFILES[0]
    const b = () => s.settings.behavior

    function iconState() {
        if (s.busy) return 'busy'
        return s.state
    }

    function rows(): [string, string][] {
        const band = { auto: 'auto', '2.4': '2.4 GHz', '5': '5 GHz', '6': '6 GHz' }[s.settings.hotspot.band] ?? s.settings.hotspot.band
        const traffic = `${fmtBytes(s.rx)} in · ${fmtBytes(s.tx)} out`
        const h = s.settings.hotspot
        switch (s.state) {
            case 'live': {
                const n = s.devices.filter(d => d.access === 'approved').length
                return [['hotspot', `${h.ssid} · ${band} · ${h.security.toUpperCase()}`], ['vpn', prof().label], ['exit ip', '185.107.56.40'],
                    ['uptime', fmtDuration(Date.now() / 1000 - s.readySince)], ['traffic', traffic],
                    ['mode', s.settings.vpn.protection + (b().dns_lock ? ' · DNS locked' : '')], ['devices', n ? `${n} connected` : 'none yet'],
                    ['uplink', 'Wi-Fi · HomeNet']]
            }
            case 'vpn':
                return [['vpn', prof().label], ['exit ip', '185.107.56.40'], ['traffic', traffic], ['hotspot', `${h.ssid} · off`], ['uplink', 'Wi-Fi · HomeNet']]
            case 'paused':
                return [['resumes', `in ${fmtDuration(s.pausedUntil - Date.now() / 1000)}`], ['profile', prof().label], ['hotspot', `${h.ssid} · off`]]
            case 'error':
                return [['problem', 'The tunnel stopped answering'], ['profile', prof().label]]
            default:
                return [['profile', prof().label], ['hotspot', `${h.ssid} · ${band} · off`], ['uplink', 'Wi-Fi · HomeNet']]
        }
    }

    function model(): ModelEntry[] {
        const m: ModelEntry[] = []
        const item = (id: string, icon: string, text: string, hint = '', kw: Partial<ModelEntry> = {}) =>
            m.push({ t: 'item', id, icon, text, hint, ...kw })
        const wait = !!s.busy
        if (s.busy) m.push({ t: 'busy', id: 'busy', text: s.busy, detail: s.journal[s.journal.length - 1]?.text ?? '' })
        const pending = s.devices.filter(d => d.access === 'pending')
        if (pending.length) m.push({ t: 'section', id: 's-pending', text: `Waiting for approval · ${pending.length}` })
        for (const p of pending) m.push({ t: 'pending', id: 'p:' + p.mac, mac: p.mac, ip: p.ip, name: p.name, device: p.device })
        if (s.state === 'live' || s.state === 'vpn' || s.state === 'error') {
            item('reconnect', 'refresh', 'Reconnect now', 'stop + start', { disabled: wait })
            if (s.state === 'live') item('hotspot_off', 'ring', 'Stop hotspot, keep VPN', '', { disabled: wait })
            else if (s.state === 'vpn') item('hotspot_on', 'play', 'Start the hotspot', 'share this VPN', { disabled: wait })
            item('pause15', 'pause', 'Pause 15 minutes', 'auto-resume', { disabled: wait })
            item('pause60', 'pause', 'Pause 1 hour', 'auto-resume', { disabled: wait })
            item('disconnect', 'power', 'Disconnect', 'VPN + hotspot off', { danger: true, disabled: wait })
        } else if (s.state === 'paused') {
            item('resume', 'play', 'Resume now', '', { disabled: wait })
            item('cancel_pause', 'x', 'Cancel auto-resume', 'stay off')
        } else {
            item('golive', 'play', 'Go live', prof().label.split(' · ')[0], { primary: true, disabled: wait })
            item('profiles', 'server', 'Choose a profile', `${PROFILES.length} available`)
        }
        item('check_ip', 'globe', 'Check exit IP', s.state === 'live' || s.state === 'vpn' ? '185.107.56.40' : '')
        item('doctor', 'pulse', 'Quick doctor', 'health check')
        item('copy_pw', 'key', 'Copy Wi-Fi password', s.settings.hotspot.ssid)
        item('cli', 'terminal', 'Open WireSpot CLI')
        item('autostart', 'power', 'Start with Windows', '', { switch: s.autostart })
        item('autoconnect', 'play', 'Go live at startup', '', { switch: b().autoconnect })
        item('approve', 'shield-check', 'Approve new devices', '', { switch: b().approve_devices })
        item('quit', 'logout', 'Quit WireSpot', s.state === 'live' || s.state === 'vpn' ? 'VPN/hotspot keep running' : '')
        return m
    }

    function snap(): Snap {
        const st = iconState()
        const tooltip = s.busy ? `WireSpot · ${s.busy}` : {
            live: `WireSpot · live · ${s.devices.filter(d => d.access === 'approved').length} device(s)`,
            vpn: 'WireSpot · VPN only', idle: 'WireSpot · off', paused: 'WireSpot · paused', error: 'WireSpot · error',
        }[s.state]
        const approved = s.devices.filter(d => d.access === 'approved')
        return {
            state: s.state, fresh: true, busy: s.busy, ssid: s.settings.hotspot.ssid, band: s.settings.hotspot.band,
            security: s.settings.hotspot.security, protection: s.settings.vpn.protection, dns_lock: b().dns_lock,
            tunnel: s.state === 'idle' || s.state === 'paused' ? '' : 'ws_' + prof().stem, profile: s.profile, profile_label: prof().label,
            provider: '', ready_since: s.state === 'live' ? s.readySince : null, last_error: s.state === 'error' ? 'The tunnel stopped answering' : '',
            paused_until: s.pausedUntil, autostart: s.autostart, rx: s.rx, tx: s.tx, handshake: s.state === 'live' ? Date.now() / 1000 - 12 : null,
            hotspot_state: s.state === 'live' ? 'On' : 'Off', endpoint: prof().endpoint, uplink: 'Wi-Fi · HomeNet',
            exit_ip: s.state === 'live' || s.state === 'vpn' ? '185.107.56.40' : '',
            pending: s.devices.filter(d => d.access === 'pending').map(d => ({ mac: d.mac, ip: d.ip, name: d.name, device: d.device })),
            blocked_n: s.blocked.length, profiles: PROFILES.map(p => [p.name, p.label] as [string, string]),
            clients: approved.map(d => [d.ip, d.name || '(no name)', d.device] as [string, string, string]),
            icon_state: st as Snap['icon_state'], tooltip, rows: rows(), model: model(),
            devices: [...s.devices, ...s.blocked], settings: structuredClone(s.settings), now: Date.now() / 1000,
        }
    }

    const push = () => emit('snap', snap())

    function hello(): Hello {
        return {
            app: 'WireSpot', version: '0.5.0', tagline: 'VPN × Mobile Hotspot', website: 'https://wirespot.vercel.app',
            terms_version: '3', privacy_version: '3', installed: false, admin: true, autostarted: false,
            proton_guide: 'https://protonvpn.com/support/wireguard-configurations',
            doctor_sections: [['quick', 'Quick'], ['wifi', 'Wi-Fi'], ['vpn', 'VPN'], ['hotspot', 'Hotspot'], ['ics', 'Sharing'], ['network', 'Network'], ['clients', 'Devices'], ['full', 'Full']],
            icons: {},
            paths: { app: 'C:\\WireSpot (browser preview)', data: '%APPDATA%\\WireSpot', vpn: '%APPDATA%\\WireSpot\\vpn', logs: '%APPDATA%\\WireSpot\\logs', downloads: '%USERPROFILE%\\Downloads' },
        }
    }

    // ----- pretend operations -----
    function run(label: string, steps: string[], done: () => void) {
        s.busy = label
        push()
        steps.forEach((step, i) => later(700 * (i + 1), () => { log(step, 'dim'); push() }))
        later(700 * (steps.length + 1), () => { s.busy = null; done(); push() })
    }

    function goLive(name?: string | null) {
        if (name) s.profile = name
        if (!s.asked) {
            // show the kind of decision the relay asks while going live
            s.asked = true
            emit('ask', {
                id: 1, question: 'Windows reports the 5 GHz band is busy on this adapter. Which band should the hotspot use?', default: 0,
                choices: [
                    { key: 'auto', label: 'Let Windows choose', hint: 'Picks a band it can host next to your uplink.' },
                    { key: '2.4', label: 'Use 2.4 GHz', hint: 'Works with every device, a little slower.' },
                    { key: 'cancel', label: 'Cancel', hint: '' },
                ],
            })
            return
        }
        run('Going live', ['Starting the tunnel ws_' + prof().stem, 'Handshake with ' + prof().endpoint, 'DNS lock on', 'Starting the Mobile Hotspot', 'Sharing the tunnel'], () => {
            s.state = 'live'
            s.readySince = Date.now() / 1000
            log(`Live: ${s.settings.hotspot.ssid} through ${prof().label}`)
            toast('WireSpot is live', `${s.settings.hotspot.ssid} shares ${prof().label}.`)
            later(2500, () => addDevice({ mac: '3c:22:fb:81:0a:11', ip: '192.168.137.23', name: 'Pixel-8', display: 'Pixel-8', device: 'Android phone', vendor: 'Google', randomized: true, access: 'approved' }))
            later(6000, () => addDevice({ mac: '8e:1f:64:c2:33:07', ip: '192.168.137.61', name: '', display: 'iPhone', device: 'iPhone', vendor: '', randomized: true, access: b().approve_devices ? 'pending' : 'approved' }))
        })
    }

    function addDevice(d: Device) {
        if (s.state !== 'live' || s.devices.some(x => x.mac === d.mac)) return
        s.devices.push(d)
        if (d.access === 'pending') {
            emit('pending', { mac: d.mac, ip: d.ip, name: d.name, device: d.device })
            log(`New device waiting: ${d.display} (${d.ip})`, 'warn')
        } else {
            log(`Device joined: ${d.display} (${d.ip})`)
        }
        push()
    }

    function stop(to: 'idle' | 'vpn' | 'paused', label: string) {
        run(label, to === 'vpn' ? ['Stopping the Mobile Hotspot'] : ['Stopping the Mobile Hotspot', 'DNS lock off', 'Stopping the tunnel'], () => {
            s.state = to
            if (to !== 'vpn') { s.devices = []; s.rx = 0; s.tx = 0 }
            else s.devices = []
            log(to === 'paused' ? 'Paused' : to === 'vpn' ? 'Hotspot off, VPN still on' : 'Disconnected')
        })
    }

    function doAction(action: string, arg?: any) {
        switch (action) {
            case 'golive': return goLive(arg)
            case 'disconnect': return stop('idle', 'Disconnecting')
            case 'hotspot_off': return stop('vpn', 'Stopping the hotspot')
            case 'hotspot_on': return run('Starting the hotspot', ['Starting the Mobile Hotspot'], () => { s.state = 'live'; s.readySince = Date.now() / 1000 })
            case 'reconnect': return run('Reconnecting', ['Stopping the tunnel', 'Starting the tunnel', 'Handshake'], () => toast('Reconnected', prof().label))
            case 'pause': {
                const min = Number(arg) || 15
                s.pausedUntil = Date.now() / 1000 + min * 60
                return stop('paused', 'Pausing')
            }
            case 'check_ip':
                if (s.state === 'live' || s.state === 'vpn') toast('Exit IP 185.107.56.40', 'Amsterdam, Netherlands · through the VPN')
                else toast('Exit IP 203.0.113.7', 'Your own connection (the VPN is off)')
                return
            case 'profile_probe':
                s.checks[arg] = { level: 'good', title: 'Reachable · 38 ms', detail: 'The server answered 2 of 2 pings. A reply shows the server is reachable, not that the tunnel works.' }
                later(900, () => emit('measurement', { kind: 'profile', name: arg, check: s.checks[arg] }))
                return
            case 'speed_test': {
                const phases = ['Measuring latency', 'Testing download', 'Testing upload']
                phases.forEach((phase, i) => later(1100 * i, () => { s.speed = { phase, result: null }; emit('measurement', { kind: 'speed', state: s.speed }) }))
                later(1100 * phases.length, () => {
                    s.speed = { phase: 'Complete', result: { download_mbps: 87.4, upload_mbps: 21.9, latency_ms: 38 } }
                    emit('measurement', { kind: 'speed', state: s.speed })
                    toast('Speed test complete', '87.4 Mbps down · 21.9 Mbps up · 38 ms latency')
                })
                return
            }
            case 'doctor': return doctor(arg || 'quick')
        }
    }

    function doctor(section: string) {
        s.report = { section, running: true, lines: [] }
        emit('report', s.report)
        later(1200, () => {
            const live = s.state === 'live'
            s.report = {
                section, running: false, lines: [
                    { level: 'head', text: `WireSpot doctor · ${section}` },
                    { level: 'ok', text: 'Running as administrator' },
                    { level: 'ok', text: 'WireGuard for Windows 0.5.3 installed' },
                    { level: live ? 'ok' : 'warn', text: live ? 'Tunnel ws_' + prof().stem + ' is up · handshake 12 s ago' : 'No tunnel running' },
                    { level: 'ok', text: 'Mobile Hotspot is supported on this adapter (2.4 and 5 GHz)' },
                    { level: live ? 'ok' : 'dim', text: live ? 'DNS lock active (NRPT rule)' : 'DNS lock is applied while live' },
                    { level: 'ok', text: 'Uplink: Wi-Fi · HomeNet' },
                    { level: 'text', text: 'Everything needed to go live is in place.' },
                ],
            }
            emit('report', s.report)
            log(`Doctor (${section}) finished`)
        })
    }

    // traffic counters tick while live
    setInterval(() => {
        if (s.state !== 'live' && s.state !== 'vpn') return
        s.rx += 180_000 + Math.random() * 900_000
        s.tx += 40_000 + Math.random() * 200_000
        push()
    }, 3000)

    log('WireSpot started', 'head')
    log('Settings loaded · profile ' + prof().label)
    later(60, () => { emit('ready', hello()); push() })

    const rpc: Record<string, (p: any) => any> = {
        hello: () => hello(),
        snapshot: () => snap(),
        visible: () => true,
        action: ({ id }) => {
            if (id.startsWith('allow:')) return rpc.device({ mac: id.slice(6), verdict: 'approve' })
            if (id.startsWith('block:')) return rpc.device({ mac: id.slice(6), verdict: 'block' })
            const simple: Record<string, [string, any?]> = {
                golive: ['golive', null], disconnect: ['disconnect'], reconnect: ['reconnect'], hotspot_off: ['hotspot_off'],
                hotspot_on: ['hotspot_on'], pause15: ['pause', 15], pause60: ['pause', 60], check_ip: ['check_ip'],
            }
            if (simple[id]) doAction(...simple[id])
            else if (id === 'resume') { s.state = 'idle'; goLive() }
            else if (id === 'cancel_pause') { s.state = 'idle'; s.pausedUntil = 0; push() }
            else if (id === 'profiles') emit('navigate', { page: 'profiles' })
            else if (id === 'doctor') { emit('navigate', { page: 'checks' }); doctor('quick') }
            else if (id === 'copy_pw') { navigator.clipboard?.writeText(s.settings.hotspot.password).catch(() => {}); toast('Password copied', s.settings.hotspot.ssid) }
            else if (id === 'cli') toast('WireSpot CLI', 'The CLI opens in a terminal in the desktop app.')
            else if (id === 'autostart') rpc.toggle({ name: 'autostart' })
            else if (id === 'autoconnect') rpc.toggle({ name: 'autoconnect' })
            else if (id === 'approve') rpc.toggle({ name: 'approve' })
            else if (id === 'quit') toast('Quit', 'In the desktop app this closes WireSpot; the VPN and hotspot keep running.')
            return true
        },
        do: ({ action, arg }) => { doAction(action, arg); return true },
        device: ({ mac, verdict }) => {
            const d = [...s.devices, ...s.blocked].find(x => x.mac === mac)
            if (verdict === 'approve' && d) {
                d.access = 'approved'
                s.blocked = s.blocked.filter(x => x.mac !== mac)
                if (!s.devices.includes(d)) s.devices.push(d)
                toast('Allowed', `${d.display} can use the VPN now.`)
            } else if (verdict === 'block' && d) {
                d.access = 'blocked'
                s.devices = s.devices.filter(x => x.mac !== mac)
                s.blocked.push(d)
                toast('Blocked', `${d.display} stays without network.`)
            } else if (verdict === 'forget') {
                s.blocked = s.blocked.filter(x => x.mac !== mac)
                s.remembered = s.remembered.filter(x => x.mac !== mac)
                toast('Forgotten', 'It will be asked about again next time.')
            }
            push()
            return true
        },
        devices: () => ({ approval: b().approve_devices, remembered: s.remembered }),
        set_behavior: ({ key, value }) => { b()[key] = !!value; push(); return true },
        toggle: ({ name }) => {
            if (name === 'autostart') s.autostart = !s.autostart
            else if (name === 'approve') b().approve_devices = !b().approve_devices
            else if (name === 'autoconnect') b().autoconnect = !b().autoconnect
            push()
            return true
        },
        settings: () => structuredClone(s.settings),
        save_hotspot: ({ ssid, password, band, security, restart }) => {
            const problems: string[] = []
            if (!ssid.trim()) problems.push('The name cannot be empty.')
            if (ssid.length > 32) problems.push('The name can be at most 32 characters.')
            if (password.length < 8 || password.length > 63) problems.push('The password must be 8 to 63 characters.')
            if (problems.length) return { ok: false, problems }
            Object.assign(s.settings.hotspot, { ssid: ssid.trim(), password, band, security })
            push()
            if (restart) doAction('reconnect')
            return { ok: true, problems: [] }
        },
        set_protection: ({ key }) => { s.settings.vpn.protection = key; push(); return true },
        profiles: () => ({
            mode: b().profile_less ? 'nordvpn' : 'wireguard', default: s.settings.vpn.default_profile,
            profiles: b().profile_less ? [] : PROFILES.map(p => ({ ...p, default: p.name === s.settings.vpn.default_profile })),
            bad: b().profile_less ? [] : [{ name: 'old-server.conf', why: 'No [Peer] section: this file is not a complete WireGuard profile.' }],
            checks: s.checks,
            provider: { status: 'connected', reason: 'NordVPN is connected to Netherlands #812 over NordLynx.', protocol: 'NordLynx', adapter: 'NordLynx' },
        }),
        set_default_profile: ({ name }) => { s.settings.vpn.default_profile = name; s.profile = name; push(); return true },
        scan_provider: () => { toast('NordVPN', 'Connected · NordLynx'); return true },
        import: ({ path }) => ({
            id: 7, file: String(path).split(/[\\/]/).pop(), origin: 'import',
            details: [['server', 'JP-FREE#4 · Japan · free'], ['endpoint', '138.199.21.4:51820'], ['address', '10.2.0.2/32'], ['dns', '10.2.0.1'],
                ['routes', 'full tunnel'], ['server key', 'Xk2pQ0bT1uV7yZcA9dE3fG…'], ['private key', 'valid, never displayed'],
                ['your key', 'mN4hJ8kL2pR6sT0vW3xY5z…'], ['checks', 'no scripts · keys valid']],
        }),
        review: ({ choice }) => {
            if (choice === 'move' || choice === 'copy') toast('Profile imported', 'JP-FREE#4 · Japan is ready to use.')
            else if (choice === 'decline') toast('Declined', 'The file was not imported.')
            return { ok: true, message: '' }
        },
        answer: ({ key }) => {
            if (key === 'cancel') { log('Going live cancelled', 'warn'); return true }
            if (key !== 'auto') s.settings.hotspot.band = key
            goLive()
            return true
        },
        // the installer (preview it with ?setup in the address)
        setup_info: () => ({
            install_dir: 'C:\\Users\\you\\AppData\\Local\\Programs\\WireSpot', data_dir: '%APPDATA%\\WireSpot',
            installed_version: '0.3.1', old_app_running: true, legacy: '', start_with_windows: true, wireguard: false,
        }),
        install: async () => {
            const steps = ['Closing a running WireSpot (your VPN and hotspot keep running)', 'Copying WireSpot',
                'Preparing your data folder', 'Creating the desktop shortcut', 'Creating the Start menu entry',
                'Setting up Start with Windows', 'Installed']
            for (const text of steps) {
                emit('setup_progress', { text })
                await new Promise(r => setTimeout(r, 450))
            }
            return { ok: true, dest: 'C:\\Users\\you\\AppData\\Local\\Programs\\WireSpot', moved: 0, shortcuts: [] }
        },
        checks: () => ({ report: s.report, speed: s.speed }),
        doctor: ({ section }) => { doctor(section); return true },
        save_report: () => {
            if (!s.report.lines.length) throw new Error('Run a check first.')
            return `%APPDATA%\\WireSpot\\logs\\doctor-${s.report.section}.txt`
        },
        activity: ({ limit = 500 }) => s.journal.slice(-limit),
        open: ({ target }) => { toast('Opens in the desktop app', `(${target})`); return true },
        uninstall: () => { later(1500, () => emit('uninstalled', { message: 'WireSpot was removed. Your settings and VPN profiles were kept.' })); return true },
        // updates: the preview is the newest version (add ?update=demo to pretend a newer one is out)
        update_check: async () => {
            await new Promise(r => setTimeout(r, 400))
            const current = hello().version
            const [major, minor] = current.split('.').map(Number)
            return fakeUpdate
                ? {
                    current, latest: `${major}.${minor + 1}.0`, available: true, size: 31_620_608,
                    notes: '**New**\n- A sample change, to show how an update looks.\n\n**Fixed**\n- A sample fix.',
                    published_at: new Date().toISOString(), page: 'https://github.com/svyixiu/WireSpot/releases/latest',
                }
                : { current, latest: current, available: false, notes: '', published_at: '', size: 0, page: '' }
        },
        update_download: () => {
            if (!fakeUpdate) throw new Error('Updates are only downloaded in the desktop app.')
            updateCancelled = false
            const total = 31_620_608
            let done = 0
            const tick = () => {
                if (updateCancelled) return emit('update_failed', { cancelled: true, error: '' })
                done = Math.min(total, done + 1_100_000)
                emit('update_progress', { downloaded: done, total })
                if (done < total) setTimeout(tick, 100)
                else emit('update_ready', { path: 'C:\\ProgramData\\WireSpot\\updates\\WireSpot-demo.exe' })
            }
            setTimeout(tick, 100)
            return true
        },
        update_cancel: () => { updateCancelled = true; return true },
        quit: () => true,
    }

    return {
        async call(method: string, params: any) {
            const fn = rpc[method]
            if (!fn) throw new Error(`unknown method: ${method}`)
            await new Promise(r => setTimeout(r, 40))
            return fn(params ?? {})
        },
    }
}

function fmtBytes(n: number) {
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
    let i = 0
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
    return `${i ? n.toFixed(1) : n} ${units[i]}`
}

function fmtDuration(sec: number) {
    sec = Math.max(0, Math.round(sec))
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
    return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`
}
