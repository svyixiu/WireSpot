import { createGlobalState } from '@vueuse/core'
import { computed, ref, shallowRef } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'

/**
 * The connection to the WireSpot engine (Python, wirespot/bridge.py), which
 * does all the networking. The Rust side starts it and relays its messages:
 * `call()` sends a request, `on()` receives pushed events, and `snap` is the
 * latest status snapshot. Outside the desktop app (browser preview) a
 * simulated engine answers instead.
 */

export type IconState = 'live' | 'vpn' | 'idle' | 'busy' | 'paused' | 'error' | 'unknown'

export interface Hello {
    app: string;
    version: string;
    tagline: string;
    website: string;
    terms_version: string;
    privacy_version: string;
    installed: boolean;
    admin: boolean;
    autostarted: boolean;
    proton_guide: string;
    doctor_sections: [string, string][];
    icons: Record<string, string>;
    paths: { app: string; data: string; vpn: string; logs: string; downloads: string };
}

/** One entry of the engine's panel model (the same rows the tray panel shows). */
export interface ModelEntry {
    t: 'item' | 'busy' | 'section' | 'pending';
    id: string;
    icon?: string;
    text?: string;
    hint?: string;
    detail?: string;
    primary?: boolean;
    danger?: boolean;
    disabled?: boolean;
    switch?: boolean;
    mac?: string;
    ip?: string;
    name?: string;
    device?: string;
}

export interface Device {
    mac: string;
    ip: string;
    name: string;
    display: string;
    device: string;
    vendor: string;
    randomized: boolean;
    access: 'approved' | 'pending' | 'blocked';
}

export interface EngineSettings {
    hotspot: { ssid: string; password: string; band: string; security: string };
    vpn: { protection: string; default_profile?: string; wireguard_path?: string };
    behavior: Record<string, boolean>;
}

export interface Snap {
    state: string;
    fresh: boolean;
    busy: string | null;
    ssid: string;
    band: string;
    security: string;
    protection: string;
    dns_lock: boolean;
    tunnel: string;
    profile: string;
    profile_label: string;
    provider: string;
    provider_protocol?: string;
    provider_status?: string;
    provider_reason?: string;
    provider_adapter?: string;
    ready_since: number | null;
    last_error: string;
    paused_until: number;
    autostart: boolean;
    rx: number;
    tx: number;
    handshake: number | null;
    hotspot_state: string;
    endpoint: string;
    uplink: string;
    exit_ip: string;
    pending: { mac: string; ip: string; name: string; device: string }[];
    blocked_n?: number;
    profiles: [string, string][];
    clients: [string, string, string][];
    icon_state: IconState;
    tooltip: string;
    rows: [string, string][];
    model: ModelEntry[];
    devices: Device[];
    settings: EngineSettings;
    /** engine clock when the snapshot was taken */
    now: number;
}

type Listener = (data: any) => void

export const inDesktopApp = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export const useEngine = createGlobalState(() => {
    const hello = shallowRef<Hello | null>(null)
    const snap = shallowRef<Snap | null>(null)
    /** set while the engine isn't running (it restarts by itself) */
    const down = ref<string | null>(null)
    const listeners = new Map<string, Set<Listener>>()
    let backend: { call: (method: string, params: object) => Promise<any> } | null = null

    function emit(event: string, data: any) {
        if (event === 'ready') {
            hello.value = data
            down.value = null
        } else if (event === 'snap') {
            snap.value = data
            down.value = null
        } else if (event === 'down') {
            down.value = data?.error || 'The WireSpot engine stopped.'
        }
        listeners.get(event)?.forEach(fn => fn(data))
    }

    /** Listen to an engine event; returns a function that stops listening. */
    function on(event: string, fn: Listener) {
        if (!listeners.has(event)) listeners.set(event, new Set())
        listeners.get(event)!.add(fn)
        return () => listeners.get(event)?.delete(fn)
    }

    async function call<T = any>(method: string, params: object = {}): Promise<T> {
        if (inDesktopApp) return invoke<T>('engine', { method, params })
        await ready
        return backend!.call(method, params)
    }

    const ready: Promise<void> = (async () => {
        if (inDesktopApp) {
            await listen<{ event: string; data: any }>('engine', e => emit(e.payload.event, e.payload.data))
            const state = await invoke<{ hello: Hello | null; snap: Snap | null; running: boolean; error: string }>('engine_state')
            if (state.hello) hello.value = state.hello
            if (state.snap) snap.value = state.snap
            if (!state.running && state.error) down.value = state.error
        } else {
            backend = (await import('@/mock/engine-mock')).createMockEngine(emit)
        }
    })()

    /** Runs a panel-model action (the same ids the tray uses: golive, disconnect, pause15…). */
    const action = (id: string) => call('action', { id })

    const state = computed<IconState>(() => snap.value?.icon_state ?? 'unknown')
    const behavior = computed(() => snap.value?.settings?.behavior ?? {})

    return { hello, snap, down, state, behavior, ready, call, on, action }
})
