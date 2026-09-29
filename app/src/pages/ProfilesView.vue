<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { open } from '@tauri-apps/plugin-dialog';
import { inDesktopApp, useEngine } from '@/composables/engine';
import { useNav } from '@/composables/nav';
import { useToasts } from '@/composables/toasts';
import { useDialogs, type Review } from '@/composables/dialogs';
import { vSmooth } from '@/directives/smooth-scroll';
import { openLink } from '@/data/links';
import { errorText } from '@/utils/format';
import Icon from '@/components/Icon.vue';

interface Profile {
    name: string; stem: string; label: string; server: string; country: string; country_code: string;
    entry_country_code: string; free: boolean; endpoint: string; full_tunnel: boolean; allowed_ips: string[];
    features: string[]; default: boolean;
}
interface Check { level: 'good' | 'warn' | 'bad'; title: string; detail: string }
interface Profiles {
    mode: 'wireguard' | 'nordvpn';
    default: string;
    profiles: Profile[];
    bad: { name: string; why: string }[];
    checks: Record<string, Check>;
    provider: { status: string; reason: string; protocol: string; adapter: string };
}

const engine = useEngine();
const { snap, hello, state } = engine;
const { page, setPage } = useNav();
const { toast } = useToasts();
const dialogs = useDialogs();

const data = ref<Profiles | null>(null);
const checking = ref<Set<string>>(new Set());

async function load() {
    try {
        data.value = await engine.call<Profiles>('profiles');
    } catch (e) {
        toast('error', "Couldn't read your profiles", errorText(e));
    }
}

// reload when the page opens and whenever the engine's list or mode changes
watch(() => [page.value === 'profiles', snap.value?.profiles?.length, engine.behavior.value.profile_less,
    snap.value?.settings?.vpn?.default_profile, snap.value?.provider_status] as const, ([visible]) => {
    if (visible) load();
}, { immediate: true });

const off = engine.on('measurement', (m: { kind: string; name?: string; check?: Check }) => {
    if (m.kind !== 'profile' || !m.name) return;
    checking.value.delete(m.name);
    checking.value = new Set(checking.value);
    if (data.value && m.check) data.value.checks = { ...data.value.checks, [m.name]: m.check };
});
onUnmounted(off);

const nord = computed(() => data.value?.mode === 'nordvpn');
const live = computed(() => state.value === 'live' || state.value === 'vpn');

function where(p: Profile) {
    const place = p.entry_country_code ? `Secure Core via ${p.entry_country_code} to ${p.country_code}` : p.country || 'Unknown country';
    return place + (p.free ? ' · free' : '');
}

async function goLive(p: Profile) {
    try {
        await engine.call('do', { action: 'golive', arg: p.name });
        setPage('home');
    } catch (e) {
        toast('error', "Couldn't go live", errorText(e));
    }
}

async function probe(p: Profile) {
    checking.value = new Set(checking.value).add(p.name);
    try {
        await engine.call('do', { action: 'profile_probe', arg: p.name });
    } catch (e) {
        checking.value.delete(p.name);
        toast('error', "Couldn't check that profile", errorText(e));
    }
}

async function makeDefault(p: Profile) {
    try {
        await engine.call('set_default_profile', { name: p.name });
        toast('success', 'Default profile', `${p.server} is used for Go live.`);
        load();
    } catch (e) {
        toast('error', "Couldn't change the default", errorText(e));
    }
}

async function importConf() {
    let path: string | null = null;
    if (inDesktopApp) {
        const picked = await open({
            title: 'Import a WireGuard .conf',
            multiple: false,
            directory: false,
            defaultPath: hello.value?.paths.downloads,
            filters: [{ name: 'WireGuard config', extensions: ['conf'] }, { name: 'All files', extensions: ['*'] }],
        }).catch(() => null);
        if (!picked || Array.isArray(picked)) return;
        path = picked;
    } else {
        path = 'C:\\Users\\you\\Downloads\\wg-JP-FREE-4.conf';
    }
    try {
        const review = await engine.call<Review>('import', { path });
        dialogs.reviews.value = [...dialogs.reviews.value, review];
    } catch (e) {
        toast('error', "Couldn't read that file", errorText(e));
    }
}

async function scan() {
    try {
        await engine.call('scan_provider');
        setTimeout(load, 1500);
    } catch (e) {
        toast('error', "Couldn't check NordVPN", errorText(e));
    }
}

const openFolder = () => engine.call('open', { target: 'vpn' }).catch(e => toast('error', "Couldn't open the folder", errorText(e)));
const status = (s: string) => (s || 'checking…').replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
const checkClass = (level: Check['level']) => ({ good: 'ok', warn: 'warn', bad: 'danger' }[level]);
</script>

<template>
    <div v-smooth class="h-full overflow-y-auto px-4 pb-4">
        <div class="max-w-[1000px] mx-auto pt-5">
            <div class="page-head">
                <div>
                    <div class="eyebrow">{{ nord ? 'Supported VPNs' : 'Profiles' }}</div>
                    <h1 class="page-title">Where your traffic goes.</h1>
                </div>
                <div v-if="data && !nord" class="flex gap-2">
                    <button class="btn btn-glass btn-sm" @click="openFolder"><Icon name="folder" class="w-4 h-4" /> VPN folder</button>
                    <button class="btn btn-primary btn-sm" @click="importConf"><Icon name="import" class="w-4 h-4" /> Import a .conf…</button>
                </div>
            </div>

            <div v-if="!data" class="glass p-6 text-sm text-muted flex items-center gap-2"><span class="spinner"></span> Reading profiles…</div>

            <!-- ===== Profile-less Mode: NordVPN ===== -->
            <template v-else-if="nord">
                <section class="paper p-7">
                    <div class="flex items-start gap-4">
                        <span class="w-12 h-12 rounded-2xl grid place-items-center bg-[var(--paper-fill-2)]"><Icon name="server" class="w-6 h-6" /></span>
                        <div class="flex-1 min-w-0">
                            <div class="eyebrow">Profile-less Mode</div>
                            <h2 class="display text-[28px] leading-[32px] text-ink mt-0.5">NordVPN</h2>
                            <p class="text-sm text-muted mt-1.5 leading-snug max-w-xl">
                                {{ data.provider.reason || 'Connect through the NordVPN desktop application, then go live to share it.' }}
                            </p>
                        </div>
                        <span class="chip !text-ok">Supported</span>
                    </div>
                    <div class="grid grid-cols-3 gap-3 mt-5">
                        <div class="rounded-2xl border border-line px-4 py-3">
                            <div class="text-xs text-muted">Connection</div>
                            <div class="text-ink font-semibold mt-0.5">{{ status(data.provider.status) }}</div>
                        </div>
                        <div class="rounded-2xl border border-line px-4 py-3">
                            <div class="text-xs text-muted">Protocol</div>
                            <div class="text-ink font-semibold mt-0.5">{{ data.provider.protocol || 'Unknown' }}</div>
                        </div>
                        <div class="rounded-2xl border border-line px-4 py-3">
                            <div class="text-xs text-muted">Adapter</div>
                            <div class="text-ink font-semibold mt-0.5">{{ data.provider.adapter || 'Not detected' }}</div>
                        </div>
                    </div>
                    <div class="flex gap-2 mt-5">
                        <button class="btn btn-primary btn-sm" @click="scan"><Icon name="refresh" class="w-4 h-4" /> Scan again</button>
                        <button class="btn btn-glass btn-sm" @click="setPage('settings')">Turn off Profile-less Mode</button>
                    </div>
                </section>
                <p class="text-xs text-muted leading-relaxed mt-4 px-2">
                    WireSpot doesn't sign in to NordVPN or read its settings. It notices the connection the NordVPN app made and
                    shares it over your hotspot. Profile-less Mode is under Settings &gt; Advanced.
                </p>
            </template>

            <!-- ===== WireGuard profiles ===== -->
            <template v-else>
                <section v-if="!data.profiles.length" class="paper p-7 mb-4">
                    <div class="eyebrow">First profile</div>
                    <h2 class="display text-[28px] leading-[32px] text-ink mt-0.5">No profiles yet.</h2>
                    <p class="text-sm text-muted mt-2 leading-snug max-w-2xl">
                        A profile is a WireGuard <span class="font-mono">.conf</span> file from your VPN provider. For Proton VPN:
                        sign in, open Downloads &gt; WireGuard configuration, choose a server or country, create the config, then
                        import the downloaded file here. New Proton configs in Downloads are offered automatically too.
                    </p>
                    <div class="flex gap-2 mt-4">
                        <button class="btn btn-primary btn-sm" @click="importConf">Import a .conf…</button>
                        <button class="btn btn-glass btn-sm" @click="openLink(hello?.proton_guide ?? 'https://protonvpn.com/support/wireguard-configurations')">
                            Proton's step-by-step guide <Icon name="external" class="w-3.5 h-3.5" />
                        </button>
                    </div>
                </section>

                <div class="grid grid-cols-2 gap-4 items-start">
                    <TransitionGroup name="list">
                        <article v-for="p in data.profiles" :key="p.name" class="glass p-5" :class="{ 'is-default': p.default }">
                            <div class="flex items-center gap-2.5">
                                <span class="w-2.5 h-2.5 rounded-full shrink-0" :class="p.default ? 'bg-accent' : 'border-2 border-line-strong'"></span>
                                <h3 class="font-semibold text-ink text-[15px] truncate flex-1">{{ p.server }}</h3>
                                <span v-if="p.default" class="tag !h-5 !text-[11px]">default</span>
                            </div>
                            <div class="text-xs text-muted mt-1 ml-5">{{ where(p) }}</div>
                            <div class="mt-3">
                                <div class="kv"><span>Endpoint</span><span class="font-mono selectable">{{ p.endpoint || '-' }}</span></div>
                                <div class="kv"><span>Routes</span><span>{{ p.full_tunnel ? 'full tunnel' : p.allowed_ips.join(', ') }}</span></div>
                                <div v-if="p.features.length" class="kv"><span>Options</span><span>{{ p.features.join(' · ') }}</span></div>
                                <div class="kv"><span>File</span><span class="font-mono">{{ p.name }}</span></div>
                            </div>
                            <Transition name="rise">
                                <div v-if="data.checks[p.name]" class="note mt-3" :class="checkClass(data.checks[p.name].level)">
                                    <div class="font-semibold">{{ data.checks[p.name].title }}</div>
                                    <div class="opacity-90 mt-0.5">{{ data.checks[p.name].detail }}</div>
                                </div>
                            </Transition>
                            <div class="flex gap-2 mt-4">
                                <button class="btn btn-primary btn-sm" :disabled="live || !!snap?.busy" @click="goLive(p)">
                                    <Icon name="play" class="w-4 h-4" /> Go live
                                </button>
                                <button class="btn btn-glass btn-sm" :disabled="checking.has(p.name)" @click="probe(p)">
                                    <span v-if="checking.has(p.name)" class="spinner !w-3 !h-3"></span>
                                    <Icon v-else name="pulse" class="w-4 h-4" /> Check
                                </button>
                                <button v-if="!p.default" class="btn btn-link btn-sm ml-auto" @click="makeDefault(p)">Make default</button>
                            </div>
                        </article>
                    </TransitionGroup>

                    <article v-for="b in data.bad" :key="'bad' + b.name" class="glass p-5 !border-[color-mix(in_srgb,var(--danger)_40%,transparent)]">
                        <div class="flex items-center gap-2 text-danger">
                            <Icon name="x" class="w-4 h-4" />
                            <h3 class="font-semibold text-[15px] truncate">{{ b.name }}</h3>
                        </div>
                        <p class="text-xs text-muted mt-1.5 leading-relaxed">{{ b.why }}</p>
                    </article>
                </div>

                <div v-if="data.profiles.length" class="flex items-center gap-3 mt-5 px-2 text-xs text-muted">
                    <span>Go live uses the default profile. Check pings the profile's server twice; some servers ignore ping.</span>
                    <button class="doc-link ml-auto shrink-0" @click="openLink(hello?.proton_guide ?? 'https://protonvpn.com/support/wireguard-configurations')">
                        How to get a Proton .conf
                    </button>
                </div>
            </template>
        </div>
    </div>
</template>

<style scoped>
.is-default {
    border-color: color-mix(in srgb, var(--accent) 45%, var(--line));
}
</style>
