<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { useEngine } from '@/composables/engine';
import { useNav } from '@/composables/nav';
import { useToasts } from '@/composables/toasts';
import { vSmooth } from '@/directives/smooth-scroll';
import { errorText } from '@/utils/format';
import Icon from '@/components/Icon.vue';
import ReportLines, { type Line } from '@/components/ReportLines.vue';

interface Speed {
    phase: string;
    result: { download_mbps: number; upload_mbps: number; latency_ms: number } | null;
    error?: string;
}
interface Report { section: string; running: boolean; lines: Line[] }

const engine = useEngine();
const { snap, state, hello } = engine;
const { page } = useNav();
const { toast } = useToasts();

const speed = ref<Speed | null>(null);
const report = ref<Report>({ section: '', running: false, lines: [] });

async function load() {
    try {
        const r = await engine.call<{ report: Report; speed: Speed | null }>('checks');
        speed.value = r.speed;
        report.value = r.report;
    } catch {
        // an older engine: start empty
    }
}
let loaded = false;
watch(() => page.value === 'checks', visible => {
    if (visible && !loaded) { loaded = true; load(); }
}, { immediate: true });

const offs = [
    engine.on('measurement', (m: { kind: string; state?: Speed }) => { if (m.kind === 'speed') speed.value = m.state ?? null; }),
    engine.on('report', (r: Report) => { report.value = r; loaded = true; }),
];
onUnmounted(() => offs.forEach(off => off()));

const connected = computed(() => !!snap.value?.fresh && (state.value === 'live' || state.value === 'vpn'));
const speedRunning = computed(() => !!speed.value?.phase && !speed.value.result && !speed.value.error);
const sections = computed(() => hello.value?.doctor_sections ?? [['quick', 'Quick'], ['full', 'Full']]);
const sectionName = computed(() => sections.value.find(([k]) => k === report.value.section)?.[1] ?? report.value.section);

function act(action: string, arg?: unknown) {
    engine.call('do', { action, arg }).catch(e => toast('error', "That didn't work", errorText(e)));
}

function doctor(section: string) {
    report.value = { section, running: true, lines: [] };
    engine.call('doctor', { section }).catch(e => toast('error', "Couldn't run that check", errorText(e)));
}

async function saveReport() {
    try {
        const path = await engine.call<string>('save_report');
        toast('success', 'Report saved', path);
    } catch (e) {
        toast('error', "Couldn't save the report", errorText(e));
    }
}
</script>

<template>
    <div class="h-full grid grid-cols-[336px_minmax(0,1fr)] gap-4 px-4 pt-3 pb-4">
        <div v-smooth class="min-h-0 overflow-y-auto flex flex-col gap-4">
            <!-- ===== Speed ===== -->
            <section class="paper p-6 shrink-0">
                <div class="eyebrow">Connection speed</div>
                <h2 class="display text-[24px] leading-[28px] text-ink mt-1">How fast is it?</h2>
                <div class="mt-4 min-h-[76px]">
                    <Transition name="rise" mode="out-in">
                        <div v-if="speed?.result" key="result" class="grid grid-cols-3 gap-2">
                            <div><div class="big">{{ speed.result.download_mbps }}</div><div class="unit">Mbps down</div></div>
                            <div><div class="big">{{ speed.result.upload_mbps }}</div><div class="unit">Mbps up</div></div>
                            <div><div class="big">{{ Math.round(speed.result.latency_ms) }}</div><div class="unit">ms latency</div></div>
                        </div>
                        <div v-else-if="speed?.error" key="error" class="note danger">Unavailable: {{ speed.error }}</div>
                        <div v-else-if="speedRunning" key="running" class="flex items-center gap-2.5 text-sm text-ink-2 pt-2">
                            <span class="spinner"></span> {{ speed?.phase }}…
                        </div>
                        <p v-else key="idle" class="text-sm text-muted leading-snug">
                            {{ connected ? 'Measures latency, download and upload through the VPN.' : 'Connect the VPN, then run a test to see latency, download and upload.' }}
                        </p>
                    </Transition>
                </div>
                <button class="btn btn-primary btn-sm mt-3" :disabled="!connected || speedRunning" @click="act('speed_test')">
                    <Icon name="pulse" class="w-4 h-4" /> {{ speed?.result ? 'Run again' : 'Run speed test' }}
                </button>
                <p class="text-[11px] text-muted mt-3 leading-snug">Uses about 6 MB with Cloudflare, only when you press Run. Results are estimates.</p>
            </section>

            <!-- ===== Tools ===== -->
            <section class="glass p-5 shrink-0">
                <div class="eyebrow mb-2">Quick tools</div>
                <button class="action" @click="act('check_ip')">
                    <Icon name="globe" /><span class="text-sm font-semibold">Check exit IP</span>
                    <span class="hint">{{ snap?.exit_ip || '' }}</span>
                </button>
                <button class="action" @click="engine.call('open', { target: 'logs' })">
                    <Icon name="folder" /><span class="text-sm font-semibold">Open the logs folder</span>
                </button>
            </section>
        </div>

        <!-- ===== Diagnostics ===== -->
        <section class="glass flex flex-col min-h-0 overflow-hidden">
            <div class="shrink-0 px-5 pt-4 pb-3 border-b border-line">
                <div class="flex items-center gap-2">
                    <h2 class="display text-[22px] text-ink">Diagnostics</h2>
                    <button class="btn btn-glass btn-sm ml-auto" :disabled="!report.lines.length || report.running" @click="saveReport">
                        <Icon name="file" class="w-4 h-4" /> Save report
                    </button>
                </div>
                <div class="flex flex-wrap gap-1.5 mt-3">
                    <button v-for="[key, label] in sections" :key="key" class="btn btn-sm !h-8"
                        :class="key === 'quick' ? 'btn-primary' : report.section === key ? 'btn-glass !border-ink-2' : 'btn-glass'"
                        :disabled="report.running" @click="doctor(key)">
                        {{ label }}
                    </button>
                </div>
            </div>
            <div v-smooth class="flex-1 min-h-0 overflow-y-auto px-5 py-4">
                <Transition name="rise" mode="out-in">
                    <div v-if="report.running" key="running" class="flex items-center gap-2.5 text-sm text-ink-2">
                        <span class="spinner"></span> Running the {{ sectionName }} check…
                    </div>
                    <ReportLines v-else-if="report.lines.length" :key="report.section + report.lines.length" :lines="report.lines" />
                    <p v-else key="empty" class="text-sm text-muted leading-relaxed max-w-md">
                        Pick a check. <span class="text-ink-2 font-semibold">Quick</span> is a one-screen health checklist;
                        <span class="text-ink-2 font-semibold">Full</span> runs everything. Saved reports have keys and passwords removed.
                    </p>
                </Transition>
            </div>
        </section>
    </div>
</template>

<style scoped>
.big {
    font-family: var(--font-display);
    font-weight: 800;
    font-size: 30px;
    line-height: 32px;
    letter-spacing: -0.02em;
    color: var(--ink);
    font-variant-numeric: tabular-nums;
}

.unit {
    font-size: 11.5px;
    color: var(--muted);
    margin-top: 2px;
}
</style>
