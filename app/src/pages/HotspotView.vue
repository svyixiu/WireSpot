<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useEngine } from '@/composables/engine';
import { useNav } from '@/composables/nav';
import { useToasts } from '@/composables/toasts';
import { vSmooth } from '@/directives/smooth-scroll';
import { errorText } from '@/utils/format';
import Icon from '@/components/Icon.vue';

const engine = useEngine();
const { snap, state } = engine;
const { page } = useNav();
const { toast } = useToasts();

const BANDS = [{ id: 'auto', label: 'Auto' }, { id: '2.4', label: '2.4 GHz' }, { id: '5', label: '5 GHz' }, { id: '6', label: '6 GHz' }];
const SECURITY = [{ id: 'wpa2', label: 'WPA2' }, { id: 'transition', label: 'WPA3/2' }, { id: 'wpa3', label: 'WPA3' }];
const PROTECTION = [
    {
        id: 'balanced', title: 'Balanced', tag: 'for the hotspot',
        text: 'Full VPN routing through two /1 routes. Phones get addresses and DNS; the DNS lock keeps lookups in the tunnel and the guard stops the hotspot if the tunnel drops.',
    },
    {
        id: 'strict', title: 'Strict', tag: 'kill-switch',
        text: "Keeps 0.0.0.0/0. Maximum leak protection for this PC, but it blocks the hotspot's DHCP and DNS, so phones can't get an address. For VPN-only use.",
    },
];

const form = reactive({ ssid: '', password: '', band: 'auto', security: 'wpa2' });
const saved = computed(() => snap.value?.settings?.hotspot);
const dirty = computed(() => !!saved.value && (['ssid', 'password', 'band', 'security'] as const).some(k => form[k] !== saved.value![k]));
const showPassword = ref(false);
const problems = ref<string[]>([]);
const saving = ref(false);

// fill the form from the engine's settings, but never over unsaved edits
function reset() {
    if (!saved.value) return;
    Object.assign(form, { ssid: saved.value.ssid, password: saved.value.password, band: saved.value.band, security: saved.value.security });
    problems.value = [];
}
watch(saved, (now, before) => {
    if (!before || !dirty.value || page.value !== 'hotspot') reset();
}, { immediate: true });
watch(() => page.value === 'hotspot', visible => { if (visible && !dirty.value) reset(); showPassword.value = false; });

const live = computed(() => state.value === 'live');

async function save(restart = false) {
    saving.value = true;
    try {
        const r = await engine.call<{ ok: boolean; problems: string[] }>('save_hotspot', { ...form, restart });
        problems.value = r.problems;
        if (r.ok) toast('success', restart ? 'Saved · restarting the hotspot' : 'Hotspot saved', restart ? undefined : live.value ? 'Restart the hotspot to use the new settings.' : 'Used the next time you go live.');
    } catch (e) {
        toast('error', "Couldn't save", errorText(e));
    } finally {
        saving.value = false;
    }
}

const protection = computed(() => snap.value?.settings?.vpn?.protection ?? 'balanced');
async function setProtection(key: string) {
    if (key === protection.value) return;
    try {
        await engine.call('set_protection', { key });
        toast('info', `Protection: ${key}`, 'Applies the next time the VPN connects (Reconnect to apply now).');
    } catch (e) {
        toast('error', "Couldn't change protection", errorText(e));
    }
}

const passwordHint = computed(() => {
    const n = form.password.length;
    return n < 8 ? `${8 - n} more character${8 - n === 1 ? '' : 's'} needed` : n > 63 ? 'At most 63 characters' : `${n} characters`;
});
</script>

<template>
    <div v-smooth class="h-full overflow-y-auto px-4 pb-4">
        <div class="max-w-[1000px] mx-auto pt-5">
            <div class="page-head">
                <div>
                    <div class="eyebrow">Hotspot</div>
                    <h1 class="page-title">Your Wi-Fi, through the VPN.</h1>
                </div>
                <span class="chip" :class="live ? '!text-ok' : ''">
                    <span class="w-1.5 h-1.5 rounded-full" :class="live ? 'bg-ok' : 'bg-faint'"></span>
                    {{ live ? `On · ${snap?.ssid}` : 'Off' }}
                </span>
            </div>

            <div class="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-4 items-start">
                <!-- ===== Network ===== -->
                <section class="glass p-6">
                    <h2 class="card-title mb-4">Network</h2>

                    <label class="block">
                        <span class="eyebrow">Name (SSID)</span>
                        <input v-model="form.ssid" class="field w-full mt-1.5" maxlength="32" spellcheck="false" autocomplete="off" />
                    </label>

                    <label class="block mt-4">
                        <span class="flex items-center justify-between">
                            <span class="eyebrow">Password</span>
                            <span class="text-[11px] text-faint">{{ passwordHint }}</span>
                        </span>
                        <span class="field flex items-center gap-2 mt-1.5 !pr-1.5">
                            <input v-model="form.password" :type="showPassword ? 'text' : 'password'" class="flex-1 min-w-0 bg-transparent outline-none"
                                spellcheck="false" autocomplete="off" />
                            <button type="button" class="icon-btn !w-8 !h-8" :aria-label="showPassword ? 'Hide password' : 'Show password'"
                                :data-tip="showPassword ? 'Hide' : 'Show'" @click="showPassword = !showPassword">
                                <Icon :name="showPassword ? 'eye-off' : 'eye'" class="w-4 h-4" />
                            </button>
                        </span>
                    </label>

                    <div class="mt-4">
                        <span class="eyebrow">Band</span>
                        <div class="seg mt-1.5">
                            <button v-for="b in BANDS" :key="b.id" :class="{ on: form.band === b.id }" @click="form.band = b.id">{{ b.label }}</button>
                        </div>
                    </div>

                    <div class="mt-4">
                        <span class="eyebrow">Security</span>
                        <div class="seg mt-1.5">
                            <button v-for="s in SECURITY" :key="s.id" :class="{ on: form.security === s.id }" @click="form.security = s.id">{{ s.label }}</button>
                        </div>
                    </div>

                    <p class="text-xs text-muted mt-3 leading-relaxed">
                        Auto lets the Wi-Fi driver pick a band it can host next to your uplink. WPA3 needs Windows 11 24H2.
                    </p>

                    <Transition name="rise">
                        <div v-if="problems.length" class="note danger mt-3">
                            <div v-for="p in problems" :key="p">{{ p }}</div>
                        </div>
                    </Transition>

                    <div class="flex items-center gap-2 mt-5">
                        <button class="btn btn-primary" :disabled="saving || !dirty" @click="save(false)">
                            <span v-if="saving" class="spinner"></span> Save
                        </button>
                        <button v-if="live" class="btn btn-glass" :disabled="saving || !dirty" @click="save(true)">Save and restart the hotspot</button>
                        <button v-if="dirty" class="btn btn-link ml-auto" @click="reset">Undo changes</button>
                    </div>
                </section>

                <!-- ===== Protection ===== -->
                <section class="glass p-6">
                    <h2 class="card-title mb-1">Protection</h2>
                    <p class="text-xs text-muted mb-4 leading-relaxed">How the VPN routes are set up. Applies the next time the VPN connects.</p>
                    <div class="space-y-2.5">
                        <button v-for="p in PROTECTION" :key="p.id" class="choice" :class="{ on: protection === p.id }" @click="setProtection(p.id)">
                            <span class="flex items-center gap-2 w-full">
                                <span class="radio" :class="{ on: protection === p.id }"></span>
                                <span class="text-[15px] font-semibold text-ink">{{ p.title }}</span>
                                <span class="text-xs text-muted">· {{ p.tag }}</span>
                            </span>
                            <span class="text-xs text-muted leading-relaxed pl-6">{{ p.text }}</span>
                        </button>
                    </div>
                    <div class="mt-4 pt-3 border-t border-line text-xs text-muted leading-relaxed">
                        The DNS lock and the fail-closed guard are in Settings &gt; Safety.
                    </div>
                </section>
            </div>
        </div>
    </div>
</template>

<style scoped>
.radio {
    width: 16px;
    height: 16px;
    border-radius: 999px;
    border: 2px solid var(--line-strong);
    flex-shrink: 0;
    transition: border-color 150ms ease, box-shadow 150ms ease;
}

.radio.on {
    border-color: var(--accent);
    box-shadow: inset 0 0 0 3px var(--glass), inset 0 0 0 8px var(--accent);
}
</style>
