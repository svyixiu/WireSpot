<script setup lang="ts">
import { fmtTime } from '@/utils/format';

/** Output lines from the engine (doctor reports, the activity journal), already stripped of glyphs. */
export interface Line {
    level: string;
    text: string;
    ts?: number;
    source?: string;
}

defineProps<{ lines: Line[] }>();
</script>

<template>
    <ol class="lines selectable">
        <li v-for="(l, i) in lines" :key="i" :class="'l-' + l.level">
            <span v-if="l.ts" class="ts">{{ fmtTime(l.ts) }}</span>
            <span v-if="l.source" class="src" :class="'s-' + l.source">{{ l.source }}</span>
            <span class="mark" aria-hidden="true"></span>
            <span class="text">{{ l.text }}</span>
        </li>
    </ol>
</template>

<style scoped>
.lines {
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.55;
}

li {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 1px 0;
    color: var(--ink-2);
}

.ts {
    color: var(--faint);
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
}

.src {
    flex-shrink: 0;
    width: 30px;
    font-size: 10.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
}

.src.s-cli {
    color: var(--accent);
}

.mark {
    flex-shrink: 0;
    width: 6px;
    height: 6px;
    border-radius: 999px;
    transform: translateY(-1px);
    background: transparent;
}

.text {
    min-width: 0;
    white-space: pre-wrap;
    word-break: break-word;
}

.l-ok .mark { background: var(--ok); }
.l-warn .mark { background: var(--warn); }
.l-warn .text { color: var(--warn); }
.l-error .mark { background: var(--danger); }
.l-error .text { color: var(--danger); }
.l-question .mark { background: var(--accent); }
.l-dim .text { color: var(--muted); }

.l-head {
    margin-top: 6px;
}

.l-head .text {
    font-family: var(--font-sans);
    font-weight: 700;
    color: var(--ink);
}

.l-head:first-child {
    margin-top: 0;
}
</style>
