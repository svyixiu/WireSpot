<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useEngine, type Device } from '@/composables/engine';
import { useNav } from '@/composables/nav';
import { useToasts } from '@/composables/toasts';
import { vSmooth } from '@/directives/smooth-scroll';
import { deviceIcon, errorText } from '@/utils/format';
import Icon from '@/components/Icon.vue';
import ToggleSwitch from '@/components/ToggleSwitch.vue';

const engine = useEngine();
const { snap, state } = engine;
const { page } = useNav();
const { toast } = useToasts();

const approval = computed(() => engine.behavior.value.approve_devices !== false);
const devices = computed(() => snap.value?.devices ?? []);
const groups = computed(() => ({
    pending: devices.value.filter(d => d.access === 'pending'),
    approved: devices.value.filter(d => d.access === 'approved'),
    blocked: devices.value.filter(d => d.access === 'blocked'),
}));

// devices allowed before that aren't here right now
const remembered = ref<{ mac: string; name: string }[]>([]);
async function loadRemembered() {
    try {
        remembered.value = (await engine.call<{ remembered: { mac: string; name: string }[] }>('devices')).remembered;
    } catch {
        remembered.value = [];
    }
}
watch(() => [page.value === 'devices', devices.value.length, approval.value] as const, ([visible]) => {
    if (visible) loadRemembered();
}, { immediate: true });

const name = (d: Device) => (d.name ? d.display : d.device) || 'Unknown device';

async function verdict(mac: string, v: 'approve' | 'block' | 'forget', label = '') {
    try {
        await engine.call('device', { mac, verdict: v, name: label });
        if (v === 'forget') loadRemembered();
    } catch (e) {
        toast('error', "Couldn't change that device", errorText(e));
    }
}

function toggleApproval() {
    engine.call('toggle', { name: 'approve' }).catch(e => toast('error', "Couldn't change device approval", errorText(e)));
}
</script>

<template>
    <div v-smooth class="h-full overflow-y-auto px-4 pb-4">
        <div class="max-w-[1000px] mx-auto pt-5">
            <div class="page-head">
                <div>
                    <div class="eyebrow">Devices</div>
                    <h1 class="page-title">Who's on your hotspot.</h1>
                </div>
                <div class="flex items-center gap-3 glass !rounded-full pl-4 pr-1.5 h-11">
                    <span class="text-sm font-semibold text-ink">Approve new devices</span>
                    <ToggleSwitch :model-value="approval" label="Approve new devices" @update:model-value="toggleApproval" />
                </div>
            </div>

            <p class="note mb-4" :class="approval ? '' : 'warn'">
                <template v-if="approval">
                    Device approval is on: a new device stays on the Wi-Fi without any network until you allow it here, in the
                    tray, or with <span class="font-mono">allow</span> in the CLI.
                </template>
                <template v-else>Device approval is off: anyone with the Wi-Fi password gets the VPN.</template>
            </p>

            <!-- waiting -->
            <Transition name="rise">
                <section v-if="groups.pending.length" class="mb-5">
                    <div class="eyebrow px-2 mb-2">Waiting · {{ groups.pending.length }}</div>
                    <TransitionGroup name="list" tag="div" class="relative grid grid-cols-2 gap-3">
                        <article v-for="d in groups.pending" :key="d.mac" class="paper p-5">
                            <div class="flex items-center gap-3">
                                <span class="w-10 h-10 rounded-full grid place-items-center bg-[var(--paper-fill-2)]">
                                    <Icon :name="deviceIcon(d.device)" class="w-5 h-5" />
                                </span>
                                <div class="min-w-0 flex-1">
                                    <div class="font-semibold text-ink truncate">{{ name(d) }}</div>
                                    <div class="text-xs text-muted truncate">wants to join · {{ d.device }}</div>
                                </div>
                                <span class="wait-dot"></span>
                            </div>
                            <div class="mt-3 text-xs space-y-0.5">
                                <div class="kv !py-0.5"><span>IP</span><span>{{ d.ip || 'no IP yet' }}</span></div>
                                <div class="kv !py-0.5"><span>MAC</span><span class="font-mono">{{ d.mac }}{{ d.randomized ? ' · private' : '' }}</span></div>
                                <div v-if="d.vendor" class="kv !py-0.5"><span>Vendor</span><span>{{ d.vendor }}</span></div>
                            </div>
                            <div class="flex gap-2 mt-4">
                                <button class="btn btn-primary btn-sm flex-1" @click="verdict(d.mac, 'approve', d.name)">
                                    <Icon name="check" class="w-4 h-4" /> Allow
                                </button>
                                <button class="btn btn-danger btn-sm flex-1" @click="verdict(d.mac, 'block', d.name)">
                                    <Icon name="ban" class="w-4 h-4" /> Block
                                </button>
                            </div>
                        </article>
                    </TransitionGroup>
                </section>
            </Transition>

            <!-- connected -->
            <section class="glass p-6 mb-4">
                <div class="flex items-center gap-2 mb-2">
                    <h2 class="card-title">Connected</h2>
                    <span class="chip !h-5 !px-2 tabular-nums">{{ groups.approved.length }}</span>
                </div>
                <div v-if="!groups.approved.length" class="text-sm text-muted py-2">
                    {{ state === 'live' ? 'No devices yet.' : 'The hotspot is off.' }}
                </div>
                <TransitionGroup name="list" tag="div" class="relative">
                    <div v-for="d in groups.approved" :key="d.mac" class="row">
                        <div class="flex items-center gap-3 min-w-0">
                            <Icon :name="deviceIcon(d.device)" class="w-5 h-5 text-accent" />
                            <div class="min-w-0">
                                <div class="row-title truncate">{{ name(d) }}</div>
                                <div class="row-desc truncate selectable">
                                    {{ d.ip || 'no IP yet' }} · {{ d.device }} · <span class="font-mono">{{ d.mac }}</span>{{ d.randomized ? ' (private)' : '' }}{{ d.vendor ? ' · ' + d.vendor : '' }}
                                </div>
                            </div>
                        </div>
                        <button v-if="approval" class="btn btn-danger btn-sm shrink-0" @click="verdict(d.mac, 'block', d.name)">Block</button>
                    </div>
                </TransitionGroup>
            </section>

            <div class="grid grid-cols-2 gap-4 items-start">
                <!-- blocked -->
                <section class="glass p-6">
                    <div class="flex items-center gap-2 mb-2">
                        <h2 class="card-title">Blocked</h2>
                        <span class="chip !h-5 !px-2 tabular-nums">{{ groups.blocked.length }}</span>
                    </div>
                    <div v-if="!groups.blocked.length" class="text-sm text-muted py-1">Nobody is blocked.</div>
                    <div v-for="d in groups.blocked" :key="d.mac" class="row">
                        <div class="min-w-0">
                            <div class="row-title truncate">{{ name(d) }}</div>
                            <div class="row-desc truncate font-mono">{{ d.mac }}</div>
                        </div>
                        <div class="flex gap-1.5 shrink-0">
                            <button class="btn btn-glass btn-sm" @click="verdict(d.mac, 'approve', d.name)">Allow</button>
                            <button class="btn btn-link btn-sm" @click="verdict(d.mac, 'forget')">Forget</button>
                        </div>
                    </div>
                </section>

                <!-- remembered -->
                <section class="glass p-6">
                    <div class="flex items-center gap-2 mb-2">
                        <h2 class="card-title">Remembered</h2>
                        <span class="chip !h-5 !px-2 tabular-nums">{{ remembered.length }}</span>
                    </div>
                    <p class="text-xs text-muted mb-1 leading-relaxed">Allowed before, not connected now. They get the VPN right away next time.</p>
                    <div v-if="!remembered.length" class="text-sm text-muted py-1">None yet.</div>
                    <div v-for="r in remembered" :key="r.mac" class="row !py-2">
                        <div class="min-w-0">
                            <div class="row-title truncate">{{ r.name || r.mac }}</div>
                            <div v-if="r.name" class="row-desc font-mono">{{ r.mac }}</div>
                        </div>
                        <button class="btn btn-link btn-sm shrink-0" @click="verdict(r.mac, 'forget')">Forget</button>
                    </div>
                </section>
            </div>

            <p v-if="approval" class="text-xs text-muted leading-relaxed mt-5 px-2">
                How it works: WireSpot points a waiting device's address at nowhere on this PC, so nothing reaches it. It can
                still see the Wi-Fi, and a device on a new random MAC address shows up as a new request.
            </p>
        </div>
    </div>
</template>
