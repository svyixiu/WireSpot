<script setup lang="ts">
import { LOGO_BOLT, LOGO_TILE } from '@/data/logo';
</script>

<template>
    <!-- WireSpot mark: a soft rounded tile with a rounded bolt, in the accent
         color (the taskbar icon is drawn the same way, brand-icon.ts) -->
    <svg viewBox="0 0 24 24" aria-hidden="true" class="logo-mark">
        <rect :x="LOGO_TILE.x" :y="LOGO_TILE.y" :width="LOGO_TILE.size" :height="LOGO_TILE.size" :rx="LOGO_TILE.rx"
            fill="var(--logo-fill, var(--accent))" />
        <path :d="LOGO_BOLT" fill="var(--logo-glyph, var(--on-accent))" />
    </svg>
</template>
