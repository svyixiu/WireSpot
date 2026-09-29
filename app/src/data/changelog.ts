/**
 * Everything that changed in WireSpot, newest first. Shown in Settings >
 * About > Changelog. Add the new version at the top when you release.
 */
export type ChangeKind = 'new' | 'improved' | 'fixed' | 'design' | 'performance' | 'security'

export interface Change {
    kind: ChangeKind;
    text: string;
}

export interface Release {
    version: string;
    /** YYYY-MM-DD */
    date: string;
    title: string;
    preview?: boolean;
    changes: Change[];
}

export const CHANGE_KINDS: { id: ChangeKind; label: string }[] = [
    { id: 'new', label: 'New' },
    { id: 'improved', label: 'Improved' },
    { id: 'fixed', label: 'Fixed' },
    { id: 'design', label: 'Design' },
    { id: 'performance', label: 'Performance' },
    { id: 'security', label: 'Security' },
]

export const CHANGELOG: Release[] = [
    {
        version: '0.5.0',
        date: '2026-09-30',
        title: 'Updates from inside the app',
        preview: true,
        changes: [
            { kind: 'new', text: 'Settings > About > Check for updates looks for a newer WireSpot on GitHub. If there is one, it shows what changed and asks before downloading anything.' },
            { kind: 'new', text: 'The download shows its progress, size, speed and time left, and carries on if you close the dialog. When it is done, WireSpot restarts into the new version by itself: it installs over the old one and keeps your shortcuts, Start with Windows, settings and profiles.' },
            { kind: 'security', text: 'Only files attached to WireSpot\'s own GitHub releases are downloaded, into a folder only administrators can change, and one that doesn\'t match the checksum GitHub lists for it is thrown away before anything runs.' },
            { kind: 'fixed', text: 'Opening a newer WireSpot.exe while WireSpot was running closed the running one but never started the new one.' },
            { kind: 'fixed', text: 'Uninstalling left WireSpot\'s program files behind in its install folder.' },
        ],
    },
    {
        version: '0.4.0',
        date: '2026-09-29',
        title: 'A new desktop app',
        preview: true,
        changes: [
            { kind: 'design', text: 'A completely new window: a wide, calm layout with tabs for Home, Devices, Profiles, Hotspot, Checks, Activity and Settings, cream status cards, pill buttons and a soft glow behind everything.' },
            { kind: 'new', text: 'Themes and accent colors, including a fully custom theme. The app icon follows the accent you pick.' },
            { kind: 'new', text: 'A startup splash that shows the app getting ready, and a Home page that shows your connection, devices and actions at a glance.' },
            { kind: 'new', text: 'The status, devices and actions update live while the window is open, and the tray menu follows the VPN state.' },
            { kind: 'improved', text: 'Decisions while going live, new .conf files and uninstalling now open as focused dialogs, with the recommended choice marked and keyboard shortcuts (1-9, Enter, Esc).' },
            { kind: 'new', text: 'Settings > About shows the version and this changelog, with links to the website, the source code, the Privacy Policy and the Terms.' },
            { kind: 'new', text: 'One download: WireSpot.exe installs itself. Started anywhere else, it shows a short installer (or runs without installing); running a newer WireSpot.exe updates the installed one.' },
            { kind: 'new', text: 'While WireSpot is in the tray, a device asking to join gets a pop-up with Allow and Block, and messages appear in the corner of the screen. They never take the focus from what you are doing.' },
            { kind: 'design', text: 'A softer logo: a cream bolt with rounded corners and tips on a rounder tile, the same in the window, the taskbar, the tray and the splash.' },
            { kind: 'fixed', text: 'Quitting takes the tray icon down first, and updating an older WireSpot never leaves its tray icon behind.' },
            { kind: 'performance', text: 'The window is built with Rust and Vue; the networking engine (tunnels, hotspot, device approval, guard) still runs the same proven Python code, now as a separate background process the window talks to.' },
            { kind: 'security', text: 'The window only ever receives what it shows: VPN private keys never leave the engine, and profiles and devices are sent field by field.' },
            { kind: 'security', text: 'Before starting its engine, WireSpot checks the file is exactly the one it shipped with and keeps it locked while it runs, so nothing can swap it for another program to gain administrator rights.' },
        ],
    },
    {
        version: '0.3.1',
        date: '2026-09-25',
        title: 'NordVPN profile-less hosting',
        changes: [
            { kind: 'new', text: 'Profile-less Mode (Settings > Advanced): share a connection made in the NordVPN desktop app, with no .conf file or NordVPN login in WireSpot.' },
            { kind: 'new', text: 'The Supported VPNs page shows NordVPN\'s connection, protocol and adapter, with Scan again.' },
            { kind: 'improved', text: 'Go live at startup also works in Profile-less Mode: it hosts once NordVPN is connected.' },
            { kind: 'security', text: 'The forwarding guard is always on in Profile-less Mode.' },
        ],
    },
    {
        version: '0.2.1',
        date: '2026-09-24',
        title: 'Connection checks and a fresh look',
        changes: [
            { kind: 'new', text: 'Checks: Quick, Wi-Fi, VPN, Hotspot, Sharing, Network, Devices and Full diagnostics, with Save report.' },
            { kind: 'new', text: 'A speed test (latency, download and upload through Cloudflare) that only runs when you press Run.' },
            { kind: 'design', text: 'A refreshed window and tray menu with SVG icons, sliding pages and live values that update in place.' },
            { kind: 'new', text: 'The wirespot.vercel.app website, a versioned Privacy Policy and Terms of Use, and the source published under GPL-3.0.' },
        ],
    },
]

export function formatReleaseDate(date: string) {
    const [y, m, d] = date.split('-').map(Number)
    return new Date(y, m - 1, d).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}
