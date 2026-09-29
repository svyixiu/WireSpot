<script setup lang="ts">
import { computed } from 'vue';
import { useEngine } from '@/composables/engine';
import { hideWindow, useSettings } from '@/composables/settings';
import { useToasts } from '@/composables/toasts';
import { useDialogs } from '@/composables/dialogs';
import { ACCENTS, THEMES, presetAsCustom, snapshotAppearance, type ThemeId } from '@/theme/themes';
import { vSmooth } from '@/directives/smooth-scroll';
import { CHANGELOG, formatReleaseDate } from '@/data/changelog';
import { LINKS, openLink } from '@/data/links';
import { errorText } from '@/utils/format';
import ToggleSwitch from '@/components/ToggleSwitch.vue';
import ColorField from '@/components/ColorField.vue';
import RangeSlider from '@/components/RangeSlider.vue';
import LogoMark from '@/components/LogoMark.vue';
import Icon from '@/components/Icon.vue';

const { settings, defaults } = useSettings();
const { toast } = useToasts();
const engine = useEngine();
const { snap, hello } = engine;
const dialogs = useDialogs();

// ----- appearance (the window's own settings) -----
const customAccent = computed(() => settings.value.accent !== null && !ACCENTS.includes(settings.value.accent));
function onCustomAccent(e: Event) {
    settings.value.accent = (e.target as HTMLInputElement).value;
}

const PRESETS = THEMES.filter(t => t.id !== 'custom' && t.id !== 'system');
function pickTheme(id: ThemeId) {
    // the first time: start from exactly what's on screen now
    if (id === 'custom' && !settings.value.customTheme) settings.value.customTheme = snapshotAppearance();
    settings.value.theme = id;
}
function customSwatch(fallback: [string, string, string]): [string, string, string] {
    const c = settings.value.customTheme;
    return c ? [c.bg, c.paper, c.accent] : fallback;
}
function startCustomFrom(id: ThemeId) {
    settings.value.customTheme = presetAsCustom(id, null);
    toast('info', `Custom theme reset to ${THEMES.find(t => t.id === id)?.name}`);
}
function resetAppearance() {
    // the custom colors stay saved for next time
    settings.value.theme = defaults.theme;
    settings.value.accent = defaults.accent;
    settings.value.reduceMotion = defaults.reduceMotion;
}
const percent = (v: number) => `${Math.round(v * 100)}%`;
const pixels = (v: number) => `${Math.round(v)} px`;

// ----- the engine's settings (settings.json, shared with the CLI) -----
const b = computed(() => engine.behavior.value);
const profileLess = computed(() => !!b.value.profile_less);

function setBehavior(key: string, value: boolean) {
    engine.call('set_behavior', { key, value }).catch(e => toast('error', "Couldn't change that setting", errorText(e)));
}
function toggle(name: 'autostart' | 'approve' | 'autoconnect') {
    engine.call('toggle', { name }).catch(e => toast('error', "Couldn't change that setting", errorText(e)));
}
function openTarget(target: string) {
    engine.call('open', { target }).catch(e => toast('error', "Couldn't open that", errorText(e)));
}

function hideNow() {
    toast('info', 'Hiding to the tray…', 'WireSpot keeps running. Click its tray icon to come back.', 1500);
    setTimeout(hideWindow, 900);
}

// ----- about -----
const appVersion = computed(() => hello.value?.version ?? CHANGELOG[0].version);
const release = computed(() => CHANGELOG.find(r => r.version === appVersion.value) ?? CHANGELOG[0]);
const updatedOn = computed(() => formatReleaseDate(release.value.date));
const agreedOn = computed(() => settings.value.termsAcceptedAt
    ? new Date(settings.value.termsAcceptedAt).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
    : null);

const FILES = [
    { target: 'data', icon: 'folder', title: 'Open the data folder', hint: 'settings, profiles, logs' },
    { target: 'vpn', icon: 'server', title: 'Open the VPN folder', hint: 'your .conf profiles' },
    { target: 'settings_file', icon: 'file', title: 'Edit settings.json', hint: 'in Notepad' },
    { target: 'logs', icon: 'list', title: 'Open the logs folder', hint: '' },
    { target: 'cli', icon: 'terminal', title: 'Open WireSpot CLI', hint: 'the same engine, as commands' },
];
</script>

<template>
    <div v-smooth class="h-full overflow-y-auto px-4 pb-4">
        <div class="max-w-[1000px] mx-auto pt-5">
            <div class="page-head">
                <div>
                    <div class="eyebrow">Settings</div>
                    <h1 class="page-title">Make it yours.</h1>
                </div>
                <button class="version-pill" data-tip="See what changed" data-tip-pos="bottom" @click="dialogs.changelogOpen.value = true">
                    <span class="font-semibold text-ink">WireSpot {{ appVersion }}</span>
                    <span class="text-muted">· updated {{ updatedOn }}</span>
                    <span class="version-cta">Changelog</span>
                </button>
            </div>

            <div class="grid grid-cols-2 gap-4 items-start">
                <!-- ===== Appearance ===== -->
                <section class="glass p-6">
                    <div class="flex items-center justify-between mb-4">
                        <h2 class="card-title">Appearance</h2>
                        <button class="btn btn-link btn-sm" @click="resetAppearance">Reset</button>
                    </div>

                    <div class="eyebrow mb-3">Theme</div>
                    <div class="grid grid-cols-4 gap-3 mb-6">
                        <button v-for="t in THEMES" :key="t.id" class="theme" :class="{ on: settings.theme === t.id }" @click="pickTheme(t.id)">
                            <!-- canvas, a cream card, and the accent dot -->
                            <span class="orb" :style="{ background: (t.id === 'custom' ? customSwatch(t.swatch) : t.swatch)[0] }">
                                <span class="orb-card" :style="{ background: (t.id === 'custom' ? customSwatch(t.swatch) : t.swatch)[1] }"></span>
                                <span class="orb-dot" :style="{ background: (t.id === 'custom' ? customSwatch(t.swatch) : t.swatch)[2] }"></span>
                                <svg v-if="t.id === 'custom'" viewBox="0 0 24 24" class="orb-pen" fill="none" stroke="currentColor"
                                    stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z" /></svg>
                            </span>
                            <span class="text-[11px] font-semibold text-ink-2 mt-1.5 truncate w-full text-center">{{ t.name }}</span>
                        </button>
                    </div>

                    <div v-if="settings.theme === 'custom'" class="rounded-2xl bg-accent-soft px-3.5 py-2.5 text-xs text-ink-2 mb-6 leading-relaxed">
                        You're using your own colors. Edit each one in <span class="font-semibold text-ink">Custom theme</span> below.
                    </div>
                    <template v-else>
                        <div class="eyebrow mb-1">Accent</div>
                        <p class="text-xs text-muted mb-3">
                            Picking one re-tints the whole app to match: background glow, surfaces, text, cards and the app icon.
                            Colors are adjusted automatically so everything stays readable.
                        </p>
                        <div class="flex flex-wrap items-center gap-2 mb-6">
                            <button class="accent-default" :class="{ on: settings.accent === null }" @click="settings.accent = null">Theme</button>
                            <button v-for="c in ACCENTS" :key="c" class="accent" :class="{ on: settings.accent === c }"
                                :style="{ background: c }" :aria-label="`Accent ${c}`" @click="settings.accent = c"></button>
                            <label class="accent custom" data-tip="Custom color" :class="{ on: customAccent }"
                                :style="customAccent ? { background: settings.accent! } : {}">
                                <svg v-if="!customAccent" viewBox="0 0 24 24" class="w-3.5 h-3.5" fill="none" stroke="currentColor"
                                    stroke-width="2.5" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
                                <input type="color" class="sr-only" :value="settings.accent ?? '#d97757'" @input="onCustomAccent" />
                            </label>
                        </div>
                    </template>

                    <div class="row">
                        <div>
                            <div class="row-title">Reduce motion</div>
                            <div class="row-desc">Turns off animations, smooth scrolling and typing effects.</div>
                        </div>
                        <ToggleSwitch v-model="settings.reduceMotion" label="Reduce motion" />
                    </div>
                </section>

                <!-- ===== Startup & window ===== -->
                <section class="glass p-6">
                    <h2 class="card-title mb-1">Startup &amp; window</h2>
                    <p class="text-xs text-muted mb-2 leading-relaxed">
                        Closing the window keeps WireSpot in the tray, so the VPN, the hotspot and device approval keep working.
                        Right-click the tray icon for quick actions and Quit.
                    </p>
                    <div class="row">
                        <div>
                            <div class="row-title">Start with Windows</div>
                            <div class="row-desc">In the tray when you sign in, as administrator, with no UAC prompt.</div>
                        </div>
                        <ToggleSwitch :model-value="!!snap?.autostart" label="Start with Windows" @update:model-value="toggle('autostart')" />
                    </div>
                    <div class="row">
                        <div>
                            <div class="row-title">Go live at startup</div>
                            <div class="row-desc">
                                {{ profileLess ? 'Host as soon as NordVPN is connected.' : 'Connect the default profile and start the hotspot.' }}
                            </div>
                        </div>
                        <ToggleSwitch :model-value="!!b.autoconnect" label="Go live at startup" @update:model-value="toggle('autoconnect')" />
                    </div>
                    <div class="row">
                        <div>
                            <div class="row-title">Hide now</div>
                            <div class="row-desc">Send the window to the tray right away.</div>
                        </div>
                        <button class="btn btn-glass btn-sm shrink-0" @click="hideNow">Hide</button>
                    </div>
                </section>

                <!-- ===== Custom theme ===== -->
                <Transition name="rise">
                    <section v-if="settings.theme === 'custom' && settings.customTheme" class="glass p-6 col-span-2">
                        <div class="flex items-start justify-between gap-6 mb-1">
                            <div>
                                <h2 class="card-title">Custom theme</h2>
                                <p class="text-xs text-muted mt-1 leading-relaxed max-w-[34rem]">
                                    One color for each part of the app. Changes show up everywhere right away, including the app icon.
                                </p>
                            </div>
                            <div class="shrink-0 text-right">
                                <div class="eyebrow mb-1.5">Start from</div>
                                <div class="flex flex-wrap justify-end gap-1.5 max-w-[22rem]">
                                    <button v-for="p in PRESETS" :key="p.id" class="preset" @click="startCustomFrom(p.id)">
                                        <span class="preset-dot" :style="{ background: p.swatch[0], boxShadow: `inset 0 0 0 3px ${p.swatch[2]}` }"></span>
                                        {{ p.name }}
                                    </button>
                                </div>
                            </div>
                        </div>

                        <div class="grid grid-cols-3 gap-5 mt-4">
                            <div>
                                <div class="eyebrow mb-2">Background</div>
                                <div class="grid gap-2">
                                    <ColorField v-model="settings.customTheme.bg" label="Window" />
                                    <div class="grid grid-cols-2 gap-2">
                                        <ColorField v-for="i in 4" :key="i" v-model="settings.customTheme.glow[i - 1]" :label="`Glow ${i}`" />
                                    </div>
                                </div>
                                <div class="grid gap-3.5 mt-4">
                                    <RangeSlider v-model="settings.customTheme.glowOpacity" label="Glow strength" :min="0" :max="0.8" :step="0.01" :format="percent" />
                                    <RangeSlider v-model="settings.customTheme.glowBlur" label="Glow softness" :min="0" :max="180" :format="pixels" />
                                </div>
                            </div>
                            <div>
                                <div class="eyebrow mb-2">Cards &amp; text</div>
                                <div class="grid grid-cols-2 gap-2">
                                    <ColorField v-model="settings.customTheme.surface" label="Cards" />
                                    <ColorField v-model="settings.customTheme.line" label="Borders" />
                                    <ColorField v-model="settings.customTheme.text" label="Text" />
                                    <ColorField v-model="settings.customTheme.muted" label="Secondary text" />
                                </div>
                                <div class="grid gap-3.5 mt-4">
                                    <RangeSlider v-model="settings.customTheme.glassOpacity" label="Card solidity" :min="0.3" :max="1" :step="0.01" :format="percent" />
                                    <RangeSlider v-model="settings.customTheme.glassBlur" label="Blur behind cards" :min="0" :max="48" :format="pixels" />
                                </div>
                            </div>
                            <div>
                                <div class="eyebrow mb-2">Highlights</div>
                                <div class="grid grid-cols-2 gap-2">
                                    <ColorField v-model="settings.customTheme.accent" label="Accent" class="col-span-2" />
                                    <ColorField v-model="settings.customTheme.button" label="Buttons" />
                                    <ColorField v-model="settings.customTheme.buttonText" label="Button text" />
                                    <ColorField v-model="settings.customTheme.paper" label="Feature cards" />
                                    <ColorField v-model="settings.customTheme.paperText" label="Their text" />
                                </div>
                                <!-- a tiny sample, so the less visible colors can be judged too -->
                                <div class="paper mt-3 p-3 flex items-center gap-2.5 !rounded-2xl">
                                    <span class="live-dot"></span>
                                    <span class="text-[12.5px] font-semibold flex-1 truncate">Feature card</span>
                                    <span class="btn btn-primary btn-sm !h-7 !px-3 !text-xs pointer-events-none">Go live</span>
                                </div>
                            </div>
                        </div>
                    </section>
                </Transition>

                <!-- ===== Safety ===== -->
                <section class="glass p-6">
                    <h2 class="card-title mb-1">Safety</h2>
                    <p class="text-xs text-muted mb-2 leading-relaxed">What keeps traffic inside the VPN, and strangers off it.</p>
                    <div class="row">
                        <div>
                            <div class="row-title">Approve new devices</div>
                            <div class="row-desc">New devices get no network until you allow them.</div>
                        </div>
                        <ToggleSwitch :model-value="b.approve_devices !== false" label="Approve new devices" @update:model-value="toggle('approve')" />
                    </div>
                    <div class="row">
                        <div>
                            <div class="row-title">Fail-closed guard</div>
                            <div class="row-desc">{{ profileLess ? 'Always on in Profile-less Mode.' : 'Stop the hotspot if the tunnel drops.' }}</div>
                        </div>
                        <ToggleSwitch :model-value="profileLess || b.guard !== false" :disabled="profileLess" label="Fail-closed guard"
                            @update:model-value="v => setBehavior('guard', v)" />
                    </div>
                    <div v-if="!profileLess" class="row">
                        <div>
                            <div class="row-title">DNS lock</div>
                            <div class="row-desc">Every lookup goes through the tunnel's resolver.</div>
                        </div>
                        <ToggleSwitch :model-value="b.dns_lock !== false" label="DNS lock" @update:model-value="v => setBehavior('dns_lock', v)" />
                    </div>
                </section>

                <!-- ===== Advanced ===== -->
                <section class="glass p-6">
                    <h2 class="card-title mb-1">Advanced</h2>
                    <p class="text-xs text-muted mb-2 leading-relaxed">Stored in settings.json, so the CLI uses the same values.</p>
                    <div class="row">
                        <div>
                            <div class="row-title">Profile-less Mode</div>
                            <div class="row-desc">Share a VPN managed by another desktop app instead of a WireGuard profile. NordVPN is supported.</div>
                        </div>
                        <ToggleSwitch :model-value="profileLess" label="Profile-less Mode" @update:model-value="v => setBehavior('profile_less', v)" />
                    </div>
                    <div v-if="!profileLess" class="row">
                        <div>
                            <div class="row-title">Watch Downloads</div>
                            <div class="row-desc">Offer new Proton .conf files for import.</div>
                        </div>
                        <ToggleSwitch :model-value="b.watch_downloads !== false" label="Watch Downloads" @update:model-value="v => setBehavior('watch_downloads', v)" />
                    </div>
                    <div class="row">
                        <div>
                            <div class="row-title">Debug logging</div>
                            <div class="row-desc">Write a detailed log to the logs folder (secrets redacted).</div>
                        </div>
                        <ToggleSwitch :model-value="!!b.debug" label="Debug logging" @update:model-value="v => setBehavior('debug', v)" />
                    </div>
                </section>

                <!-- ===== Your files ===== -->
                <section class="glass p-6">
                    <h2 class="card-title mb-1">Your files</h2>
                    <p class="text-xs text-muted mb-2 leading-relaxed">
                        Everything WireSpot keeps is in
                        <span class="font-mono text-ink-2 selectable break-all">{{ hello?.paths.data ?? '%APPDATA%\\WireSpot' }}</span>.
                    </p>
                    <div class="-mx-2.5">
                        <button v-for="f in FILES" :key="f.target" class="action" @click="openTarget(f.target)">
                            <Icon :name="f.icon" />
                            <span class="text-sm font-semibold">{{ f.title }}</span>
                            <span v-if="f.hint" class="hint">{{ f.hint }}</span>
                        </button>
                    </div>
                </section>

                <!-- ===== Installation ===== -->
                <section class="glass p-6">
                    <h2 class="card-title mb-1">Installation</h2>
                    <p class="text-xs text-muted leading-relaxed">
                        <template v-if="hello?.installed">
                            Installed in <span class="font-mono text-ink-2 selectable break-all">{{ hello.paths.app }}</span>.
                        </template>
                        <template v-else>
                            This is a portable copy, running from
                            <span class="font-mono text-ink-2 selectable break-all">{{ hello?.paths.app ?? '…' }}</span>. Delete its folder to remove it.
                        </template>
                    </p>
                    <div v-if="hello && !hello.admin" class="note warn mt-3">
                        Not running as administrator, so WireSpot can't start tunnels or the hotspot. Start it again as administrator.
                    </div>
                    <div v-if="hello?.installed" class="row mt-3">
                        <div>
                            <div class="row-title">Uninstall WireSpot</div>
                            <div class="row-desc">Stops WireSpot's tunnel, hotspot and DNS lock first, then removes the app.</div>
                        </div>
                        <button class="btn btn-danger btn-sm shrink-0" @click="dialogs.uninstallOpen.value = true">Uninstall…</button>
                    </div>
                </section>

                <!-- ===== About ===== -->
                <section class="glass p-6 col-span-2">
                    <div class="flex items-center gap-5">
                        <LogoMark class="w-14 h-14 shrink-0" />
                        <div class="min-w-0">
                            <div class="eyebrow">About</div>
                            <h2 class="display text-[30px] leading-[32px] text-ink mt-0.5">WireSpot</h2>
                            <p class="text-sm text-muted mt-1">
                                Version <span class="text-ink-2 font-semibold">{{ appVersion }}</span> · updated {{ updatedOn }}
                                · {{ hello?.tagline ?? 'VPN × Mobile Hotspot' }}
                            </p>
                        </div>
                        <div class="ml-auto flex flex-wrap justify-end gap-2">
                            <button class="btn btn-primary btn-sm" @click="dialogs.changelogOpen.value = true">Changelog</button>
                            <button class="btn btn-glass btn-sm" @click="openLink(LINKS.website)">Website</button>
                            <button class="btn btn-glass btn-sm" @click="openLink(LINKS.source)">Source code</button>
                        </div>
                    </div>

                    <div class="row mt-5">
                        <div class="min-w-0">
                            <div class="row-title">Terms &amp; privacy</div>
                            <div class="row-desc">
                                <template v-if="agreedOn">You agreed to the Terms of Use on {{ agreedOn }}.</template>
                                WireSpot has no accounts, telemetry or analytics, and your VPN keys stay on this PC. Read them again any time:
                            </div>
                            <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[13px]">
                                <button class="doc-link" @click="openLink(LINKS.terms)">Terms of Use</button>
                                <button class="doc-link" @click="openLink(LINKS.privacy)">Privacy Policy</button>
                                <button class="doc-link" @click="openLink(LINKS.license)">GPL-3.0 license</button>
                            </div>
                        </div>
                        <button class="btn btn-glass btn-sm shrink-0" @click="settings.termsVersion = null">Review the terms</button>
                    </div>
                    <p class="text-xs text-muted leading-relaxed pt-3 border-t border-line">
                        WireSpot is free software under the GNU General Public License v3.0 (GPL-3.0-only). It is an unofficial project,
                        not affiliated with or endorsed by Nord Security, Proton AG, WireGuard LLC, Jason A. Donenfeld or Microsoft.
                    </p>
                </section>
            </div>
        </div>
    </div>
</template>

<style scoped>
.theme {
    display: flex;
    flex-direction: column;
    align-items: center;
    min-width: 0;
}

.orb {
    position: relative;
    display: block;
    width: 56px;
    height: 56px;
    border-radius: 16px;
    overflow: hidden;
    border: 1px solid var(--line-strong);
    transition: transform 350ms var(--ease-spring), box-shadow 200ms ease;
}

.orb-card {
    position: absolute;
    left: 10px;
    right: 10px;
    bottom: -6px;
    height: 30px;
    border-radius: 8px;
}

.orb-dot {
    position: absolute;
    top: 9px;
    right: 9px;
    width: 10px;
    height: 10px;
    border-radius: 999px;
}

.orb-pen {
    position: absolute;
    left: 9px;
    top: 9px;
    width: 14px;
    height: 14px;
    color: var(--ink);
    mix-blend-mode: difference;
}

.theme:hover .orb {
    transform: translateY(-2px);
}

.theme.on .orb {
    box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--ink);
}

.accent {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border-radius: 999px;
    color: var(--ink-2);
    transition: transform 300ms var(--ease-spring), box-shadow 150ms ease;
}

.accent:hover {
    transform: scale(1.12);
}

.accent.on {
    box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--ink);
}

.accent.custom {
    box-shadow: inset 0 0 0 1.5px var(--line-strong);
}

.accent.custom.on {
    box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--ink);
}

.accent-default {
    height: 28px;
    padding: 0 12px;
    border-radius: 999px;
    background: var(--glass-2);
    box-shadow: inset 0 0 0 1px var(--line);
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--ink-2);
    transition: box-shadow 150ms ease, background 150ms ease;
}

.accent-default.on {
    background: var(--glass-3);
    color: var(--ink);
    box-shadow: 0 0 0 2px var(--accent);
}

.version-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 34px;
    padding: 0 5px 0 14px;
    border-radius: 999px;
    border: 1px solid var(--line-strong);
    font-size: 0.8rem;
    white-space: nowrap;
    transition: border-color 150ms ease, background-color 150ms ease, transform 220ms var(--ease-spring);
}

.version-pill:hover {
    border-color: var(--ink-2);
    background: var(--glass-2);
}

.version-pill:active {
    transform: scale(0.97);
}

.version-cta {
    margin-left: 4px;
    height: 24px;
    padding: 0 10px;
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    background: var(--btn);
    color: var(--btn-ink);
    font-weight: 700;
    font-size: 0.74rem;
}

.preset {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    height: 1.75rem;
    padding: 0 0.6rem 0 0.35rem;
    border-radius: 999px;
    border: 1px solid var(--line-strong);
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--ink-2);
    transition: background-color 150ms ease, color 150ms ease, transform 220ms var(--ease-spring);
}

.preset:hover {
    background: var(--glass-2);
    color: var(--ink);
}

.preset:active {
    transform: scale(0.95);
}

.preset-dot {
    width: 16px;
    height: 16px;
    border-radius: 999px;
}
</style>
