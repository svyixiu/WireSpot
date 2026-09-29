/** Same guess as wirespot/model.py device_icon. */
export function deviceIcon(device: string) {
    const d = (device || '').toLowerCase()
    return ['pc', 'laptop', 'mac', 'windows', 'raspberry', 'deck'].some(k => d.includes(k)) ? 'laptop' : 'phone'
}

export function plural(n: number, word: string, many = word + 's') {
    return `${n} ${n === 1 ? word : many}`
}

export function fmtDuration(sec: number) {
    sec = Math.max(0, Math.round(sec))
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
    return h ? `${h}h ${m}m` : m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}

export function fmtTime(ts: number) {
    return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function errorText(e: unknown) {
    return e instanceof Error ? e.message : String(e)
}

const ACRONYMS: Record<string, string> = { vpn: 'VPN', ip: 'IP', dns: 'DNS', mac: 'MAC', ssid: 'SSID', nordvpn: 'NordVPN' }
/** An engine key like "exit ip" as a label: "Exit IP". */
export function kvLabel(key: string) {
    const words = key.split(' ').map(w => ACRONYMS[w.toLowerCase()] ?? w)
    return words.join(' ').replace(/^[a-z]/, c => c.toUpperCase())
}
