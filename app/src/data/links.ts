import { openUrl } from '@tauri-apps/plugin-opener'
import { inDesktopApp } from '@/composables/engine'

export const WEBSITE = 'https://wirespot.vercel.app'

export const LINKS = {
    website: WEBSITE,
    privacy: `${WEBSITE}/privacy`,
    terms: `${WEBSITE}/terms`,
    source: 'https://github.com/svyixiu/WireSpot',
    license: 'https://www.gnu.org/licenses/gpl-3.0.html',
    wireguard: 'https://www.wireguard.com/install/',
}

/** Opens a page in the default browser. */
export function openLink(url: string) {
    if (inDesktopApp) openUrl(url).catch(() => window.open(url, '_blank'))
    else window.open(url, '_blank', 'noopener')
}
