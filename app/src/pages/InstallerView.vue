<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { useEngine } from '@/composables/engine';
import { useShell } from '@/composables/shell';
import { useSettings } from '@/composables/settings';
import { LINKS, openLink } from '@/data/links';
import { errorText } from '@/utils/format';
import AnimatedCheckbox from '@/components/AnimatedCheckbox.vue';
import LogoMark from '@/components/LogoMark.vue';

interface SetupInfo {
    install_dir: string;
    data_dir: string;
    installed_version: string;
    old_app_running: boolean;
    legacy: string;
    start_with_windows: boolean;
    wireguard: boolean;
}
interface InstallResult { ok: boolean; still_running?: boolean; error?: string; moved?: number; exe?: string }

const engine = useEngine();
const shell = useShell();
const { recordAgreement } = useSettings();

type Step = 'welcome' | 'terms' | 'installing' | 'done' | 'error';
const step = ref<Step>('welcome');
const setup = ref<SetupInfo | null>(null);
const desktop = ref(true);
const startMenu = ref(true);
const startWithWindows = ref(false);
const agreed = ref(false);
const progress = ref<string[]>([]);
const error = ref('');
const stillRunning = ref(false);
const moved = ref(0);
const launching = ref(false);

const stepIndex = computed(() => ({ welcome: 0, terms: 1, installing: 2, done: 2, error: 2 })[step.value]);
const version = computed(() => shell.info.value?.version ?? engine.hello.value?.version ?? '');
const updating = computed(() => !!setup.value?.installed_version);

// what's on this PC already (the engine answers once it's running)
watch(engine.hello, async hello => {
    if (!hello || setup.value) return;
    try {
        setup.value = await engine.call<SetupInfo>('setup_info');
        startWithWindows.value = setup.value.start_with_windows;
    } catch {
        // the installer still works without these details
    }
}, { immediate: true });

const off = engine.on('setup_progress', (p: { text: string }) => { progress.value = [...progress.value, p.text]; });
onUnmounted(off);

async function install(forceClose = false) {
    if (engine.hello.value?.terms_version) recordAgreement(engine.hello.value.terms_version);
    progress.value = [];
    error.value = '';
    stillRunning.value = false;
    step.value = 'installing';
    try {
        // give the progress screen a moment, so the change isn't jarring
        const [result] = await Promise.all([
            engine.call<InstallResult>('install', {
                desktop: desktop.value, start_menu: startMenu.value, start_with_windows: startWithWindows.value,
                force_close: forceClose,
            }),
            new Promise(r => setTimeout(r, 900)),
        ]);
        if (result.ok) {
            moved.value = result.moved ?? 0;
            step.value = 'done';
        } else {
            error.value = result.error ?? 'Something went wrong.';
            stillRunning.value = !!result.still_running;
            step.value = 'error';
        }
    } catch (e) {
        error.value = errorText(e);
        step.value = 'error';
    }
}

async function launch() {
    launching.value = true;
    try {
        await shell.launchInstalled();
    } catch (e) {
        error.value = errorText(e);
        step.value = 'error';
        launching.value = false;
    }
}

// Started by an update from the installed WireSpot: the Terms were agreed to there,
// so it installs right away (keeping the shortcuts and Start with Windows as they
// are) and opens the new version.
const autoUpdate = computed(() => !!shell.info.value?.update);
let autoUpdateStarted = false;

async function runUpdate() {
    progress.value = [];
    error.value = '';
    stillRunning.value = false;
    step.value = 'installing';
    try {
        const [result] = await Promise.all([
            engine.call<InstallResult>('install', { update: true }),
            new Promise(r => setTimeout(r, 700)),
        ]);
        if (result.ok) await launch();
        else {
            error.value = result.error ?? 'Something went wrong.';
            step.value = 'error';
        }
    } catch (e) {
        error.value = errorText(e);
        step.value = 'error';
    }
}

// the engine has to be up first
watch([engine.hello, autoUpdate], ([hello, auto]) => {
    if (hello && auto && !autoUpdateStarted) {
        autoUpdateStarted = true;
        runUpdate();
    }
}, { immediate: true });

function retry() {
    if (autoUpdate.value) runUpdate();
    else install();
}

const features = [
    ['Share your VPN over Wi-Fi', 'A WireGuard profile, or the NordVPN app you already use, for your phone, console or tablet.'],
    ['You decide who joins', 'New devices wait for your approval before they get any network.'],
    ['Fails closed', "If the tunnel drops, the hotspot stops, so nothing leaks around the VPN."],
    ['Window, tray and CLI in sync', 'Use whichever you like; they always agree on what is going on.'],
];
</script>

<template>
    <div class="h-full grid grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] gap-4 px-4 pt-3 pb-4">
        <!-- What you're installing -->
        <section class="glass flex flex-col justify-between p-8 overflow-hidden">
            <div>
                <LogoMark class="w-12 h-12 mb-6" />
                <div class="eyebrow mb-2">WireSpot {{ version ? `v${version}` : '' }}</div>
                <h1 class="display text-[44px] leading-[44px] text-ink">Your hotspot, through your VPN.</h1>
            </div>
            <ul class="space-y-4">
                <li v-for="[title, text] in features" :key="title" class="flex gap-3">
                    <span class="mt-1.5 w-2 h-2 rounded-full bg-accent shrink-0"></span>
                    <div>
                        <div class="text-[15px] font-semibold text-ink">{{ title }}</div>
                        <div class="text-sm text-muted leading-snug">{{ text }}</div>
                    </div>
                </li>
            </ul>
        </section>

        <!-- Steps -->
        <section class="paper flex flex-col overflow-hidden">
            <span class="absolute top-[18px] right-[18px] w-3 h-3 rounded-full bg-ink" aria-hidden="true"></span>
            <div class="flex items-center gap-2 px-8 pt-7">
                <template v-for="(label, i) in ['Install', 'Terms', 'Done']" :key="label">
                    <span v-if="i > 0" class="h-px w-6 bg-line-strong"></span>
                    <span class="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.06em] transition-colors"
                        :class="i <= stepIndex ? 'text-ink' : 'text-faint'">
                        <span class="w-5 h-5 rounded-full grid place-items-center text-[11px] transition-colors"
                            :class="i < stepIndex ? 'bg-ink text-[var(--paper)]' : i === stepIndex ? 'border border-ink' : 'border border-line-strong'">
                            <svg v-if="i < stepIndex" viewBox="0 0 16 16" class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2.4"
                                stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-7" /></svg>
                            <template v-else>{{ i + 1 }}</template>
                        </span>
                        {{ label }}
                    </span>
                </template>
            </div>

            <div class="flex-1 min-h-0 overflow-y-auto px-8 pt-6 pb-2">
                <Transition name="rise" mode="out-in">
                    <!-- 1. Install -->
                    <div v-if="step === 'welcome'" key="welcome">
                        <h2 class="display text-[32px] leading-[34px] text-ink">{{ updating ? 'Update WireSpot.' : 'Install WireSpot.' }}</h2>
                        <p class="text-[15px] text-muted mt-2 leading-snug">
                            Installs for your Windows user. Your settings and VPN profiles are kept in
                            <span class="font-mono text-[13px] text-ink-2">%APPDATA%\WireSpot</span>.
                        </p>

                        <div class="mt-4 space-y-2">
                            <div v-if="updating" class="note">
                                WireSpot {{ setup?.installed_version }} is installed. This updates it to {{ version }}; your settings and
                                profiles stay.
                            </div>
                            <div v-if="setup?.old_app_running" class="note">
                                An older WireSpot is running. It will be closed first; your VPN and hotspot keep running.
                            </div>
                            <div v-if="setup?.legacy" class="note">
                                VPN profiles found in <span class="font-mono">{{ setup.legacy }}</span> will be moved over (moved, so each
                                private key exists once).
                            </div>
                            <div v-if="setup && !setup.wireguard" class="note warn">
                                For .conf profiles, install
                                <button class="doc-link !text-inherit" @click="openLink(LINKS.wireguard)">WireGuard for Windows</button>
                                (free). Not needed if you share the NordVPN app's connection.
                            </div>
                        </div>

                        <div class="mt-4 space-y-2">
                            <label class="option" @click.prevent="desktop = !desktop">
                                <AnimatedCheckbox :checked="desktop" label="Desktop shortcut" @toggle="desktop = !desktop" @click.stop />
                                <span class="text-sm text-ink">Add a desktop shortcut</span>
                            </label>
                            <label class="option" @click.prevent="startMenu = !startMenu">
                                <AnimatedCheckbox :checked="startMenu" label="Start menu shortcut" @toggle="startMenu = !startMenu" @click.stop />
                                <span class="text-sm text-ink">Add to the Start menu</span>
                            </label>
                            <label class="option" @click.prevent="startWithWindows = !startWithWindows">
                                <AnimatedCheckbox :checked="startWithWindows" label="Start with Windows"
                                    @toggle="startWithWindows = !startWithWindows" @click.stop />
                                <span class="text-sm text-ink">Start with Windows <span class="text-muted">· in the tray, as administrator, no UAC prompt</span></span>
                            </label>
                        </div>
                        <div class="mt-4 text-xs text-muted">
                            Installs to <span class="font-mono text-ink-2 selectable">{{ setup?.install_dir ?? '%LOCALAPPDATA%\\Programs\\WireSpot' }}</span>
                        </div>
                    </div>

                    <!-- 2. Terms -->
                    <div v-else-if="step === 'terms'" key="terms">
                        <div class="eyebrow mb-1.5">Terms &amp; privacy</div>
                        <h2 class="display text-[28px] leading-[30px] text-ink mb-3">WireSpot changes how Windows networks.</h2>
                        <div class="space-y-2.5 text-[14px] text-ink-2 leading-snug">
                            <p>
                                It runs as administrator. While it's live it starts a VPN tunnel, turns on the Mobile Hotspot, changes routes
                                and locks DNS, and undoes all of it when you disconnect.
                            </p>
                            <p>
                                You're responsible for using it lawfully and for checking the rules of your internet provider, VPN provider,
                                school or employer before sharing a connection. Device approval is a local control, not a guarantee.
                            </p>
                            <p class="text-muted text-[13px]">No accounts, telemetry or analytics. Your VPN keys, settings and device list stay on this PC.</p>
                        </div>
                        <label class="option mt-4" @click.prevent="agreed = !agreed">
                            <AnimatedCheckbox :checked="agreed" label="I agree to the Terms of Use" @toggle="agreed = !agreed" @click.stop />
                            <span class="text-[14px] text-ink leading-snug">
                                I agree to the
                                <button class="doc-link" @click.stop="openLink(LINKS.terms)">Terms of Use</button>
                                and have read the
                                <button class="doc-link" @click.stop="openLink(LINKS.privacy)">Privacy Policy</button>.
                            </span>
                        </label>
                    </div>

                    <!-- Installing -->
                    <div v-else-if="step === 'installing'" key="installing" class="h-full flex flex-col justify-center">
                        <div class="flex items-center gap-3 text-ink">
                            <span class="spinner !w-5 !h-5"></span>
                            <h2 class="display text-[28px] leading-[30px]">{{ updating || autoUpdate ? 'Updating…' : 'Installing…' }}</h2>
                        </div>
                        <TransitionGroup name="list" tag="ul" class="relative mt-4 space-y-1 text-sm text-muted">
                            <li v-for="(line, i) in progress" :key="i" class="flex items-center gap-2">
                                <span class="w-1.5 h-1.5 rounded-full" :class="i === progress.length - 1 ? 'bg-ink' : 'bg-line-strong'"></span>
                                {{ line }}
                            </li>
                        </TransitionGroup>
                    </div>

                    <!-- Done -->
                    <div v-else-if="step === 'done'" key="done" class="h-full flex flex-col justify-center">
                        <div class="eyebrow mb-2">All set</div>
                        <h2 class="display text-[40px] leading-[42px] text-ink">WireSpot is {{ updating ? 'updated' : 'installed' }}.</h2>
                        <p class="text-[15px] text-muted mt-3 leading-snug max-w-md">
                            <template v-if="moved">Your {{ moved }} VPN profile{{ moved === 1 ? ' was' : 's were' }} moved over. </template>
                            Open it any time from {{ desktop && startMenu ? 'your desktop or the Start menu' : desktop ? 'your desktop' : startMenu ? 'the Start menu' : 'its install folder' }}.
                            To remove it later, use Settings → Uninstall, or Windows' Apps &amp; features.
                        </p>
                    </div>

                    <!-- Error -->
                    <div v-else key="error" class="h-full flex flex-col justify-center">
                        <div class="eyebrow mb-2">Something went wrong</div>
                        <h2 class="display text-[32px] leading-[34px] text-ink">{{ stillRunning ? 'WireSpot is still running.' : "Couldn't finish." }}</h2>
                        <p class="text-sm mt-3 selectable break-words" :class="stillRunning ? 'text-muted' : 'text-danger'">
                            <template v-if="stillRunning">
                                The WireSpot that's running didn't close when asked. Close it and install anyway? Only the app is closed:
                                your VPN and hotspot keep running.
                            </template>
                            <template v-else>{{ error }}</template>
                        </p>
                    </div>
                </Transition>
            </div>

            <div class="flex items-center gap-2 px-8 pb-7 pt-4">
                <template v-if="step === 'welcome'">
                    <button class="btn btn-link" @click="shell.runPortable()">Run without installing</button>
                    <button v-if="shell.info.value?.installed_exists && !updating" class="btn btn-glass ml-auto" :disabled="launching"
                        @click="launch">Open installed</button>
                    <button class="btn btn-primary min-w-[8rem]" :class="{ 'ml-auto': !(shell.info.value?.installed_exists && !updating) }"
                        @click="step = 'terms'">{{ updating ? 'Update' : 'Install' }}</button>
                </template>
                <template v-else-if="step === 'terms'">
                    <button class="btn btn-glass" @click="step = 'welcome'">Back</button>
                    <button class="btn btn-primary ml-auto" :disabled="!agreed" @click="install()">Agree &amp; {{ updating ? 'update' : 'install' }}</button>
                </template>
                <template v-else-if="step === 'done'">
                    <button class="btn btn-link" @click="shell.quit()">Close</button>
                    <button class="btn btn-primary ml-auto min-w-[10rem]" :disabled="launching" @click="launch">
                        <span v-if="launching" class="spinner"></span>
                        Open WireSpot
                    </button>
                </template>
                <template v-else-if="step === 'error'">
                    <button class="btn btn-glass" @click="step = 'welcome'">Back</button>
                    <button v-if="stillRunning" class="btn btn-danger ml-auto" @click="install(true)">Close it and install</button>
                    <button v-else class="btn btn-primary ml-auto" @click="retry()">Try again</button>
                </template>
            </div>
        </section>
    </div>
</template>

<style scoped>
.option {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.75rem 1rem;
    border-radius: 16px;
    border: 1px solid var(--line-strong);
    cursor: pointer;
    transition: background-color 150ms ease;
}

.option:hover {
    background: var(--glass-2);
}
</style>
