<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, useTemplateRef, watch } from 'vue';
import { useEngine } from '@/composables/engine';
import { useNav } from '@/composables/nav';
import { useToasts } from '@/composables/toasts';
import { vSmooth } from '@/directives/smooth-scroll';
import { errorText } from '@/utils/format';
import Icon from '@/components/Icon.vue';
import ReportLines, { type Line } from '@/components/ReportLines.vue';

const engine = useEngine();
const { page } = useNav();
const { toast } = useToasts();

const lines = ref<(Line & { ts: number; source: string })[]>([]);
const source = ref<'all' | 'app' | 'cli'>('all');
const query = ref('');
const scroller = useTemplateRef<HTMLElement>('scroller');
let signature = '';

const shown = computed(() => {
    const q = query.value.trim().toLowerCase();
    return lines.value.filter(l => (source.value === 'all' || l.source === source.value) && (!q || l.text.toLowerCase().includes(q)));
});

async function load() {
    try {
        const next = await engine.call<typeof lines.value>('activity', { limit: 500 });
        const sig = `${next.length}:${next[next.length - 1]?.ts ?? 0}`;
        if (sig === signature) return;
        signature = sig;
        const el = scroller.value;
        const atEnd = !el || el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
        lines.value = next;
        // stay at the newest line unless you scrolled up to read
        if (atEnd) nextTick(() => { if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight; });
    } catch {
        // the engine is restarting; try again on the next tick
    }
}

// the journal is shared with the CLI, so poll it while the page is open
let timer: ReturnType<typeof setInterval> | null = null;
watch(() => page.value === 'activity', visible => {
    if (timer) clearInterval(timer);
    timer = null;
    if (visible) {
        load();
        timer = setInterval(load, 2000);
    }
}, { immediate: true });
onUnmounted(() => { if (timer) clearInterval(timer); });

const openLogs = () => engine.call('open', { target: 'logs' }).catch(e => toast('error', "Couldn't open the logs folder", errorText(e)));
</script>

<template>
    <div class="h-full flex flex-col px-4 pt-3 pb-4">
        <section class="glass flex flex-col flex-1 min-h-0 overflow-hidden">
            <div class="shrink-0 flex items-center gap-3 px-5 pt-4 pb-3 border-b border-line">
                <div>
                    <h2 class="display text-[22px] text-ink leading-none">Activity</h2>
                    <p class="text-xs text-muted mt-1.5">What WireSpot did, in the app and in the CLI. Secrets are redacted.</p>
                </div>
                <label class="field ml-auto flex items-center gap-2 !h-9 w-56">
                    <svg viewBox="0 0 24 24" class="w-4 h-4 text-muted shrink-0" fill="none" stroke="currentColor" stroke-width="2.2"
                        stroke-linecap="round"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
                    <input v-model="query" class="flex-1 min-w-0 bg-transparent outline-none text-sm" placeholder="Filter…" spellcheck="false" />
                </label>
                <div class="seg !p-[2px]">
                    <button :class="{ on: source === 'all' }" class="!h-7" @click="source = 'all'">All</button>
                    <button :class="{ on: source === 'app' }" class="!h-7" @click="source = 'app'">App</button>
                    <button :class="{ on: source === 'cli' }" class="!h-7" @click="source = 'cli'">CLI</button>
                </div>
                <button class="btn btn-glass btn-sm" @click="openLogs"><Icon name="folder" class="w-4 h-4" /> Logs</button>
            </div>
            <div ref="scroller" v-smooth class="flex-1 min-h-0 overflow-y-auto px-5 py-4">
                <ReportLines v-if="shown.length" :lines="shown" />
                <p v-else class="text-sm text-muted">{{ lines.length ? 'Nothing matches.' : 'Nothing yet.' }}</p>
            </div>
        </section>
    </div>
</template>
