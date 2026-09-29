<script setup lang="ts">
import { computed } from 'vue';
import { useEventListener } from '@vueuse/core';
import { useEngine } from '@/composables/engine';
import { useDialogs } from '@/composables/dialogs';
import BaseModal from './BaseModal.vue';

// A decision the engine needs while going live (for example which band to use).
// The engine waits for the answer; the recommended choice is marked.
const engine = useEngine();
const { asks } = useDialogs();
const ask = computed(() => asks.value[0] ?? null);

/** Esc picks the choice that backs out, like the old sheet did. */
const cancelKey = computed(() => {
    const a = ask.value;
    if (!a) return '';
    return a.choices.find(c => ['stop', 'cancel', 'vpn-only', 'decline'].includes(c.key))?.key ?? a.choices[a.default]?.key ?? '';
});

function answer(key: string) {
    const a = ask.value;
    if (!a) return;
    asks.value = asks.value.slice(1);
    engine.call('answer', { id: a.id, key }).catch(() => {});
}

useEventListener(window, 'keydown', (e: KeyboardEvent) => {
    const a = ask.value;
    if (!a || e.ctrlKey || e.altKey || e.metaKey) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= a.choices.length) answer(a.choices[n - 1].key);
    else if (e.key === 'Enter') answer(a.choices[a.default].key);
    else if (e.key === 'Escape') answer(cancelKey.value);
    else return;
    e.preventDefault();
    e.stopPropagation();
}, { capture: true });
</script>

<template>
    <BaseModal :open="!!ask" eyebrow="WireSpot needs a decision" :title="ask?.question ?? ''" width="34rem" persistent>
        <div v-if="ask" class="space-y-2">
            <button v-for="(c, i) in ask.choices" :key="c.key" class="choice" :class="{ on: i === ask.default }" @click="answer(c.key)">
                <span class="flex items-center gap-2.5 w-full">
                    <span class="kbd">{{ i + 1 }}</span>
                    <span class="text-[15px] font-semibold text-ink">{{ c.label }}</span>
                    <span v-if="i === ask.default" class="ml-auto text-[11px] font-bold uppercase tracking-wide text-muted">Recommended</span>
                </span>
                <span v-if="c.hint" class="text-sm text-muted pl-[2.1rem] leading-snug">{{ c.hint }}</span>
            </button>
        </div>
        <p class="text-xs text-muted mt-4">Click, or press 1-{{ ask?.choices.length }} · Enter picks the recommended one · Esc backs out</p>
    </BaseModal>
</template>
