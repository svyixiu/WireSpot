import { createGlobalState, useStorage } from '@vueuse/core'
import { watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { applyAppearance, normalizeCustomTheme, THEMES, type CustomTheme, type ThemeId } from '@/theme/themes'

/**
 * How the app looks, kept in the window's own storage. Everything about the
 * VPN and the hotspot lives in the engine's settings.json instead, so the
 * CLI sees the same values.
 */
export const SETTINGS_KEY = 'wirespot.ui.v1'

/** WireSpot's clay orange */
export const BRAND_ACCENT = '#d97757'

export interface AppSettings {
    theme: ThemeId;
    /** hex color, or null to use the theme's own accent */
    accent: string | null;
    /** colors of the Custom theme (null until it's first picked) */
    customTheme: CustomTheme | null;
    reduceMotion: boolean;
    /** when the user agreed to the Terms, and which version (null = not yet) */
    termsAcceptedAt: number | null;
    termsVersion: string | null;
}

const DEFAULTS: AppSettings = {
    theme: 'espresso',
    accent: BRAND_ACCENT,
    customTheme: null,
    reduceMotion: false,
    termsAcceptedAt: null,
    termsVersion: null,
}

/** Also used by the splash window, which starts before the app. */
export function readSavedAppearance() {
    try {
        const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? '{}') as Partial<AppSettings>
        const theme = THEMES.some(t => t.id === s.theme) ? s.theme! : DEFAULTS.theme
        const custom = s.customTheme ? normalizeCustomTheme(s.customTheme, s.customTheme as CustomTheme) : null
        return { theme, accent: s.accent === undefined ? DEFAULTS.accent : s.accent, reduceMotion: !!s.reduceMotion, custom }
    } catch {
        return { theme: DEFAULTS.theme, accent: DEFAULTS.accent, reduceMotion: false, custom: null }
    }
}

export const useSettings = createGlobalState(() => {
    const settings = useStorage<AppSettings>(SETTINGS_KEY, { ...DEFAULTS }, undefined, { mergeDefaults: true })

    if (!THEMES.some(t => t.id === settings.value.theme)) settings.value.theme = DEFAULTS.theme

    watch(
        () => [settings.value.theme, settings.value.accent, settings.value.reduceMotion, settings.value.customTheme] as const,
        ([theme, accent, reduceMotion, custom]) => applyAppearance(theme, accent, reduceMotion, custom),
        { immediate: true, deep: true },
    )

    function recordAgreement(version: string) {
        settings.value.termsAcceptedAt = Date.now()
        settings.value.termsVersion = version
    }

    return { settings, defaults: DEFAULTS, recordAgreement }
})

export function hideWindow() {
    return invoke('hide_window').catch(() => {})
}

export function showWindow() {
    return invoke('show_window').catch(() => {})
}
