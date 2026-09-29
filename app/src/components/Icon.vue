<script setup lang="ts">
import { computed } from 'vue';
import { ICONS } from '@/data/icons';

// WireSpot's own icon set (wirespot/icons.py), so names match the engine's
const props = defineProps<{ name: string }>();
const markup = computed(() => ICONS[props.name] ?? ICONS.dot);
</script>

<template>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
        stroke-linejoin="round" aria-hidden="true" class="shrink-0" v-html="markup"></svg>
</template>
