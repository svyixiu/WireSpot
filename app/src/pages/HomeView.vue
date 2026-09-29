<script setup lang="ts">
import { computed } from 'vue';
import { useNow } from '@vueuse/core';
import { useEngine, type ModelEntry } from '@/composables/engine';
import { useNav } from '@/composables/nav';
import { useToasts } from '@/composables/toasts';
import { vSmooth } from '@/directives/smooth-scroll';
import { deviceIcon, errorText, fmtDuration, kvLabel, plural } from '@/utils/format';
import Icon from '@/components/Icon.vue';
import ToggleSwitch from '@/components/ToggleSwitch.vue';

const engine = useEngine();
const { snap, state } = engine;
const { setPage } = useNav();
const { toast } = useToasts();
const now = useNow({ interval: 1000 });

// The engine decides which actions exist right now (the same list as the tray
// panel, wirespot/model.py home_model); this page only arranges them.
const items = computed(() => (snap.value?.model ?? []).filter((e): e is ModelEntry & { t: 'item' } => e.t === 'item'));
const byId = computed(() => new Map(items.value.map(i => [i.id, i])));
const STATE_IDS = ['golive', 'profiles', 'resume', 'cancel_pause', 'reconnect', 'hotspot_on', 'hotspot_off', 'pause15', 'pause60', 'disconnect'];
const TOOL_IDS = ['check_ip', 'doctor', 'copy_pw', 'cli'];
const stateActions = computed(() => items.value.filter(i => STATE_IDS.includes(i.id)));
const tools = computed(() => items.value.filter(i => TOOL_IDS.includes(i.id)));
const switches = computed(() => items.value.filter(i => i.switch !== undefined));
const busy = computed(() => snap.value?.model?.find(e => e.t === 'busy') ?? null);

const profileLess = computed(() => !!engine.behavior.value.profile_less);
const approvalOn = computed(() => engine.behavior.value.approve_devices !== false);

async function run(id: string) {
    try {
        await engine.action(id);
    } catch (e) {
        toast('error', "That didn't work", errorText(e));
    }
}

// ----- the paper hero -----
const hero = computed(() => {
    const s = snap.value;
    const ssid = s?.ssid || 'your hotspot';
    const label = s?.profile_label || 'your VPN';
    switch (state.value) {
        case 'live': return { title: "You're live.", text: `${ssid} is sharing ${label}. Phones and consoles that join use the VPN.` };
        case 'vpn': return { title: 'VPN only.', text: `This PC is connected through ${label}; the hotspot is off.` };
        case 'paused': {
            const left = s?.paused_until ? s.paused_until - now.value.getTime() / 1000 : 0;
            return { title: 'Paused.', text: left > 0 ? `Everything is off. It comes back by itself in ${fmtDuration(left)}.` : 'Everything is off.' };
        }
        case 'error': return { title: 'Something went wrong.', text: s?.last_error || 'The last session ended unexpectedly. Reconnect, or run a check to find out why.' };
        case 'busy': return { title: 'Working on it…', text: busy.value?.text ?? '' };
        case 'idle': return {
            title: 'Ready when you are.',
            text: profileLess.value
                ? 'Connect in the NordVPN app, then go live to share it over your hotspot.'
                : `Go live to share ${label} over ${ssid}.`,
        };
        default: return { title: 'Checking…', text: 'Reading the current state of the VPN and the hotspot.' };
    }
});

// the hero's two buttons, picked from what the engine offers
const heroPrimary = computed(() => ['golive', 'resume', 'hotspot_on', 'disconnect'].map(id => byId.value.get(id)).find(Boolean) ?? null);
const heroSecondary = computed(() => {
    const id = { golive: 'profiles', resume: 'cancel_pause', hotspot_on: 'disconnect', disconnect: 'pause15' }[heroPrimary.value?.id ?? ''];
    return id ? byId.value.get(id) ?? null : null;
});

// ----- devices -----
const pending = computed(() => snap.value?.pending ?? []);
const connected = computed(() => (snap.value?.devices ?? []).filter(d => d.access === 'approved'));
const deviceName = (name: string, device: string) => (name && name !== '(no name)' ? name : device || 'Unknown device');

function verdict(mac: string, v: 'approve' | 'block', name: string) {
    engine.call('device', { mac, verdict: v, name }).catch(e => toast('error', "Couldn't change that device", errorText(e)));
}
</script>

<template>
    <div class="h-full grid grid-cols-[336px_minmax(0,1fr)] gap-4 px-4 pt-3 pb-4">
        <!-- ===== Controls ===== -->
        <section class="glass flex flex-col min-h-0 overflow-hidden">
            <div class="shrink-0 flex items-center gap-2 px-5 pt-4 pb-3 border-b border-line">
                <h2 class="display text-[22px] text-ink">Controls</h2>
                <span v-if="busy" class="chip ml-auto !h-6"><span class="spinner !w-2.5 !h-2.5 !border-[1.5px]"></span>busy</span>
            </div>

            <div v-smooth class="flex-1 min-h-0 overflow-y-auto px-2.5 py-2">
                <div class="eyebrow px-2.5 pt-1 pb-1.5">Connection</div>
                <TransitionGroup name="list" tag="div" class="relative flex flex-col">
                    <button v-for="i in stateActions" :key="i.id" class="action" :class="{ danger: i.danger }"
                        :disabled="i.disabled" @click="run(i.id)">
                        <Icon :name="i.icon!" />
                        <span class="text-sm font-semibold truncate">{{ i.text }}</span>
                        <span v-if="i.hint" class="hint">{{ i.hint }}</span>
                    </button>
                </TransitionGroup>
                <div v-if="!snap" class="px-3 py-2 text-sm text-muted flex items-center gap-2">
                    <span class="spinner"></span> Connecting to the engine…
                </div>

                <div class="eyebrow px-2.5 pt-4 pb-1.5">Tools</div>
                <button v-for="i in tools" :key="i.id" class="action" :disabled="i.disabled" @click="run(i.id)">
                    <Icon :name="i.icon!" />
                    <span class="text-sm font-semibold truncate">{{ i.text }}</span>
                    <span v-if="i.hint" class="hint">{{ i.hint }}</span>
                </button>
            </div>

            <div class="shrink-0 border-t border-line px-5 py-1.5">
                <div v-for="i in switches" :key="i.id" class="flex items-center gap-3 py-2">
                    <Icon :name="i.icon!" class="w-4 h-4 text-ink-2" />
                    <span class="text-[13px] font-semibold text-ink flex-1 truncate">{{ i.text }}</span>
                    <ToggleSwitch :model-value="!!i.switch" :label="i.text" @update:model-value="run(i.id)" />
                </div>
            </div>
        </section>

        <!-- ===== State ===== -->
        <div class="grid grid-rows-[216px_minmax(0,1fr)] gap-4 min-h-0">
            <section class="paper overflow-hidden">
                <Transition name="rise" mode="out-in">
                    <div :key="hero.title" class="h-full flex flex-col justify-center px-8">
                        <span class="absolute top-[18px] right-[18px] flex items-center gap-2 text-xs font-semibold text-muted">
                            <template v-if="state === 'live'"><span class="live-dot"></span>live</template>
                            <template v-else-if="state === 'busy' || state === 'unknown'"><span class="spinner !w-3 !h-3"></span></template>
                            <span v-else class="w-3 h-3 rounded-full bg-ink" aria-hidden="true"></span>
                        </span>
                        <div class="eyebrow mb-2">WireSpot · VPN × Mobile Hotspot</div>
                        <h1 class="display text-[40px] leading-[42px] text-ink">{{ hero.title }}</h1>
                        <p class="text-[15px] text-muted mt-2.5 max-w-xl leading-snug line-clamp-2">{{ hero.text }}</p>
                        <p v-if="busy?.detail" class="text-xs text-faint mt-1 truncate max-w-xl">{{ busy.detail }}</p>
                        <div v-if="heroPrimary || heroSecondary" class="flex items-center gap-2 mt-4">
                            <button v-if="heroPrimary" class="btn btn-primary" :disabled="heroPrimary.disabled" @click="run(heroPrimary.id)">
                                <Icon :name="heroPrimary.icon!" class="w-4 h-4" />
                                {{ heroPrimary.text }}
                            </button>
                            <button v-if="heroSecondary" class="btn btn-glass" :disabled="heroSecondary.disabled" @click="run(heroSecondary.id)">
                                {{ heroSecondary.text }}
                            </button>
                        </div>
                    </div>
                </Transition>
            </section>

            <div class="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 min-h-0">
                <!-- status lines -->
                <section class="glass flex flex-col min-h-0 overflow-hidden">
                    <div class="px-5 pt-4 pb-1 flex items-baseline justify-between gap-2">
                        <div class="eyebrow">Status</div>
                        <button class="btn btn-link btn-sm !h-6 !px-1" @click="setPage('checks')">Run checks</button>
                    </div>
                    <div v-smooth class="flex-1 min-h-0 overflow-y-auto px-5 pb-4">
                        <div v-for="[k, v] in snap?.rows ?? []" :key="k" class="kv border-b border-line last:border-b-0">
                            <span>{{ kvLabel(k) }}</span>
                            <span class="selectable" :title="v">{{ v }}</span>
                        </div>
                    </div>
                </section>

                <!-- devices -->
                <section class="glass flex flex-col min-h-0 overflow-hidden">
                    <div class="px-5 pt-4 pb-1 flex items-baseline justify-between gap-2">
                        <div class="eyebrow">Devices</div>
                        <button class="btn btn-link btn-sm !h-6 !px-1" @click="setPage('devices')">All devices</button>
                    </div>
                    <div v-smooth class="flex-1 min-h-0 overflow-y-auto px-3 pb-3">
                        <TransitionGroup name="list" tag="div" class="relative flex flex-col gap-1.5">
                            <div v-for="p in pending" :key="'p' + p.mac" class="waiting rounded-2xl px-3 py-2.5">
                                <div class="flex items-center gap-2.5">
                                    <Icon :name="deviceIcon(p.device)" class="w-4 h-4 text-warn" />
                                    <div class="min-w-0 flex-1">
                                        <div class="text-[13px] font-semibold text-ink truncate">{{ deviceName(p.name, p.device) }}</div>
                                        <div class="text-[11px] text-muted truncate">wants to join · {{ p.ip || 'no IP yet' }}</div>
                                    </div>
                                </div>
                                <div class="flex gap-1.5 mt-2">
                                    <button class="btn btn-primary btn-sm !h-7 flex-1" @click="verdict(p.mac, 'approve', p.name)">Allow</button>
                                    <button class="btn btn-danger btn-sm !h-7 flex-1" @click="verdict(p.mac, 'block', p.name)">Block</button>
                                </div>
                            </div>
                            <div v-for="d in connected" :key="d.mac" class="flex items-center gap-2.5 px-2 py-1.5">
                                <Icon :name="deviceIcon(d.device)" class="w-4 h-4 text-accent" />
                                <div class="min-w-0 flex-1">
                                    <div class="text-[13px] font-semibold text-ink truncate">{{ d.display || d.device }}</div>
                                    <div class="text-[11px] text-muted truncate">{{ d.ip || 'no IP yet' }} · {{ d.device }}</div>
                                </div>
                            </div>
                        </TransitionGroup>
                        <div v-if="!pending.length && !connected.length" class="px-2 pt-2 text-sm text-muted leading-snug">
                            <template v-if="state === 'live'">No devices yet. Join <span class="text-ink-2 font-semibold">{{ snap?.ssid }}</span> from a phone.</template>
                            <template v-else>The hotspot is off.</template>
                            <div class="text-xs text-faint mt-1.5">
                                {{ approvalOn ? 'New devices wait for your approval.' : 'Anyone with the password gets the VPN.' }}
                            </div>
                        </div>
                        <div v-else-if="connected.length" class="px-2 pt-2 text-[11px] text-faint">{{ plural(connected.length, 'device') }} connected</div>
                    </div>
                </section>
            </div>
        </div>
    </div>
</template>

<style scoped>
.waiting {
    border: 1px solid color-mix(in srgb, var(--warn) 45%, transparent);
    background: var(--warn-soft);
}
</style>
