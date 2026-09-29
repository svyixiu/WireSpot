import { createGlobalState } from '@vueuse/core'
import { ref, shallowRef } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { inDesktopApp } from './engine'

/** Mirrors shell_info in src-tauri/src/lib.rs */
export interface ShellInfo {
    /** WireSpot.exe was started outside its install folder: show the installer */
    setup: boolean;
    version: string;
    exe: string;
    installed_exe: string;
    installed_exists: boolean;
    dev: boolean;
    /** started by an update (Settings → Check for updates): install over the old copy and open it */
    update?: boolean;
}

export type ShellMode = 'loading' | 'setup' | 'app'

/**
 * The same WireSpot.exe is the installer and the app. Started from anywhere
 * other than its install folder it shows the installer first (the Rust side
 * decides, and starts the engine in its light setup mode). In a browser
 * preview, add ?setup to the address to see the installer.
 */
export const useShell = createGlobalState(() => {
    const info = shallowRef<ShellInfo | null>(null)
    const mode = ref<ShellMode>('loading')

    const ready = (inDesktopApp
        ? invoke<ShellInfo>('shell_info')
        : Promise.resolve<ShellInfo>({
            setup: new URLSearchParams(location.search).has('setup'),
            version: '0.5.0', exe: 'C:\\Users\\you\\Downloads\\WireSpot.exe',
            installed_exe: 'C:\\Users\\you\\AppData\\Local\\Programs\\WireSpot\\WireSpot.exe',
            installed_exists: false, dev: true,
            // ?setup&update=apply: what a downloaded update does (installs itself and reopens)
            update: new URLSearchParams(location.search).get('update') === 'apply',
        }))
        .then(i => {
            info.value = i
            mode.value = i.setup ? 'setup' : 'app'
        })
        .catch(() => { mode.value = 'app' })

    /** Use WireSpot straight from where it is, without installing. */
    async function runPortable() {
        if (inDesktopApp) await invoke('run_portable')
        mode.value = 'app'
    }

    /** Open the installed copy and close this one. */
    function launchInstalled() {
        if (!inDesktopApp) return Promise.resolve()
        return invoke('launch_installed')
    }

    function quit() {
        if (inDesktopApp) invoke('quit_app').catch(() => {})
    }

    return { info, mode, ready, runPortable, launchInstalled, quit }
})
