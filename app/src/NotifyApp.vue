<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useTemplateRef, watch } from 'vue';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { deviceIcon } from '@/utils/format';
import Icon from '@/components/Icon.vue';
import LogoMark from '@/components/LogoMark.vue';

interface Pending { mac: string; ip: string; name: string; device: string }
interface Message { id: number; title: string; body: string; error: boolean }

const pending = ref<Pending[]>([]);
/** "Later": not shown again here (it still waits in the app) */
const later = ref(new Set<string>());
const busy = ref(new Set<string>());
const messages = ref<Message[]>([]);
const stack = useTemplateRef<HTMLElement>('stack');
let nextId = 1;

const devices = computed(() => pending.value.filter(p => !later.value.has(p.mac)));
const nameOf = (p: Pending) => (p.name && p.name !== '(no name)' ? p.name : p.device || 'A device');

function addMessage(event: string, data: any) {
    const m: Message = event === 'toast'
        ? { id: nextId++, title: data?.title ?? 'WireSpot', body: data?.body ?? '', error: !!data?.error }
        : { id: nextId++, title: data?.message ?? 'WireSpot', body: '', error: data?.kind === 'error' };
    messages.value = [...messages.value, m].slice(-3);
    setTimeout(() => { messages.value = messages.value.filter(x => x.id !== m.id); }, m.error ? 9000 : 6000);
}

async function verdict(p: Pending, v: 'approve' | 'block') {
    busy.value = new Set(busy.value).add(p.mac);
    try {
        await invoke('engine', { method: 'device', params: { mac: p.mac, verdict: v, name: p.name } });
        pending.value = pending.value.filter(x => x.mac !== p.mac);
    } catch {
        // stays on the card; the app shows the error
    } finally {
        const next = new Set(busy.value);
        next.delete(p.mac);
        busy.value = next;
    }
}

const openApp = () => invoke('show_window').catch(() => {});
const dismiss = (id: number) => { messages.value = messages.value.filter(m => m.id !== id); };

// the window fits its cards; with none left it hides
async function fit() {
    await nextTick();
    const height = stack.value && (devices.value.length || messages.value.length) ? stack.value.offsetHeight : 0;
    invoke('notify_resize', { height }).catch(() => {});
}
watch([devices, messages], fit, { deep: true });

const offs: Promise<() => void>[] = [];
onMounted(async () => {
    offs.push(listen<{ event: string; data: any }>('engine', ({ payload }) => {
        const { event, data } = payload;
        if (event === 'snap') pending.value = data?.pending ?? [];
        else if (event === 'toast' || event === 'notify') addMessage(event, data);
    }));
    try {
        const state = await invoke<{ pending: Pending[]; messages: { event: string; data: any }[] }>('notify_state');
        pending.value = state.pending ?? [];
        state.messages.forEach(m => addMessage(m.event, m.data));
    } catch {
        // browser preview (notify.html): sample cards, to see the design
        pending.value = [{ mac: '8e:1f:64:c2:33:07', ip: '192.168.137.61', name: '', device: 'iPhone' }];
        addMessage('toast', { title: 'WireSpot is live', body: 'WireSpot-4F2A shares NL-FREE#12 · Netherlands.', error: false });
    }
    fit();
});
onUnmounted(() => offs.forEach(p => p.then(off => off())));
</script>

<template>
    <div ref="stack" class="stack">
        <TransitionGroup name="card">
            <article v-for="p in devices" :key="p.mac" class="card paper">
                <div class="flex items-center gap-3">
                    <span class="w-9 h-9 rounded-full grid place-items-center bg-[var(--paper-fill-2)] shrink-0">
                        <Icon :name="deviceIcon(p.device)" class="w-[18px] h-[18px]" />
                    </span>
                    <div class="min-w-0 flex-1">
                        <div class="eyebrow !text-[10.5px]">New device · waiting</div>
                        <div class="text-[15px] font-semibold text-ink truncate">{{ nameOf(p) }} wants to join</div>
                    </div>
                    <span class="wait-dot"></span>
                </div>
                <p class="text-xs text-muted mt-2 leading-snug">
                    {{ p.device || 'Unknown device' }} · {{ p.ip || 'no IP yet' }}. It has no network until you allow it.
                </p>
                <div class="flex items-center gap-2 mt-3">
                    <button class="btn btn-primary btn-sm !h-8 flex-1" :disabled="busy.has(p.mac)" @click="verdict(p, 'approve')">
                        <Icon name="check" class="w-4 h-4" /> Allow
                    </button>
                    <button class="btn btn-danger btn-sm !h-8 flex-1" :disabled="busy.has(p.mac)" @click="verdict(p, 'block')">
                        <Icon name="ban" class="w-4 h-4" /> Block
                    </button>
                    <button class="btn btn-link btn-sm !h-8 !px-2" @click="later = new Set(later).add(p.mac)">Later</button>
                </div>
            </article>

            <article v-for="m in messages" :key="m.id" class="card glass" :class="{ error: m.error }">
                <div class="flex items-start gap-3">
                    <LogoMark class="w-6 h-6 shrink-0 mt-0.5" />
                    <button class="min-w-0 flex-1 text-left" @click="openApp">
                        <div class="text-[14px] font-semibold text-ink leading-snug">{{ m.title }}</div>
                        <div v-if="m.body" class="text-xs text-muted mt-0.5 leading-snug line-clamp-3">{{ m.body }}</div>
                    </button>
                    <button class="icon-btn !w-7 !h-7 -mr-1 -mt-1" aria-label="Dismiss" @click="dismiss(m.id)">
                        <Icon name="x" class="w-3.5 h-3.5" />
                    </button>
                </div>
            </article>
        </TransitionGroup>
    </div>
</template>

<style>
html,
body.notify-body {
    background: transparent !important;
    overflow: hidden;
}
</style>

<style scoped>
.stack {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 6px;
}

.card {
    padding: 14px 16px;
    border-radius: 18px;
    box-shadow: 0 10px 30px -12px rgba(0, 0, 0, 0.55);
}

.card.glass {
    background: var(--glass);
}

.card.error {
    border-color: color-mix(in srgb, var(--danger) 55%, transparent);
}

.card-enter-active {
    transition: opacity 260ms ease, transform 420ms var(--ease-quint);
}

.card-leave-active {
    transition: opacity 180ms ease, transform 180ms ease;
}

.card-enter-from {
    opacity: 0;
    transform: translateX(24px);
}

.card-leave-to {
    opacity: 0;
    transform: translateX(24px);
}
</style>
