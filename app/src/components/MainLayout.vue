<script setup lang="ts">
import { computed, ref } from 'vue';
import { useEventListener } from '@vueuse/core';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { invoke } from '@tauri-apps/api/core';
import { useNav, type Page } from '@/composables/nav';
import { useEngine } from '@/composables/engine';
import AppBackground from './AppBackground.vue';
import ToastHost from './ToastHost.vue';
import LogoMark from './LogoMark.vue';
import TooltipLayer from './TooltipLayer.vue';

/** minimal: just the title bar (the installer) */
const props = defineProps<{ minimal?: boolean }>();

const { page, setPage } = useNav();
const engine = useEngine();
const { snap, state, down } = engine;

const profileLess = computed(() => !!engine.behavior.value.profile_less);
const tabs = computed<{ id: Page; label: string }[]>(() => [
  { id: 'home', label: 'Home' },
  { id: 'devices', label: 'Devices' },
  { id: 'profiles', label: profileLess.value ? 'VPNs' : 'Profiles' },
  { id: 'hotspot', label: 'Hotspot' },
  { id: 'checks', label: 'Checks' },
  { id: 'activity', label: 'Activity' },
  { id: 'settings', label: 'Settings' },
]);
const activeIndex = computed(() => Math.max(0, tabs.value.findIndex(t => t.id === page.value)));
const waiting = computed(() => snap.value?.pending?.length ?? 0);

// macOS greys out the traffic lights while the window isn't focused
const focused = ref(document.hasFocus());
useEventListener(window, 'focus', () => { focused.value = true; });
useEventListener(window, 'blur', () => { focused.value = false; });

function win() {
  try {
    return getCurrentWindow();
  } catch {
    return null; // not inside the desktop app
  }
}
// closing hides WireSpot to the tray: the VPN, the hotspot and device approval keep running
// the installer has no tray to hide in: its close button quits
const close = () => {
  if (props.minimal) invoke('quit_app').catch(() => win()?.close());
  else win()?.close().catch(() => {});
};
const minimize = () => win()?.minimize().catch(() => {});

const STATE_LABEL: Record<string, string> = {
  live: 'Live', vpn: 'VPN only', idle: 'Off', paused: 'Paused', error: 'Problem', busy: 'Working', unknown: 'Checking',
};
const stateLabel = computed(() => STATE_LABEL[state.value] ?? 'Checking');
const stateTip = computed(() => snap.value?.tooltip?.replace(/\n/g, ' · ') || 'Waiting for the WireSpot engine');

useEventListener(window, 'keydown', (e: KeyboardEvent) => {
  if (props.minimal || !(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
  const index = ['1', '2', '3', '4', '5', '6', '7'].indexOf(e.key);
  if (index >= 0) {
    e.preventDefault();
    setPage(tabs.value[index].id);
  }
});
</script>

<template>
  <div class="relative h-dvh flex flex-col overflow-hidden">
    <AppBackground />

    <!-- Title bar: the whole strip drags the window -->
    <!-- the separator is exactly where scrolled content gets cut off: pages put
         their top spacing inside (pt-3), never as a gap under the bar -->
    <header data-tauri-drag-region
      class="relative z-30 h-14 shrink-0 flex items-center px-4 gap-4 border-b border-line bg-[color-mix(in_srgb,var(--bg)_70%,transparent)] backdrop-blur-md">
      <div class="lights flex items-center gap-2" :class="{ blurred: !focused }">
        <button class="light close" :aria-label="minimal ? 'Close' : 'Close to the tray'" :data-tip="minimal ? undefined : 'Close (keeps running in the tray)'"
          data-tip-pos="bottom" @click="close">
          <svg viewBox="0 0 12 12"><path d="M3.5 3.5l5 5M8.5 3.5l-5 5" /></svg>
        </button>
        <button class="light min" aria-label="Minimize" @click="minimize">
          <svg viewBox="0 0 12 12"><path d="M3 6h6" /></svg>
        </button>
        <!-- zoom is disabled: the window has a fixed size -->
        <button class="light zoom" aria-label="Zoom (disabled)" disabled></button>
      </div>

      <div data-tauri-drag-region class="flex items-center gap-2 min-w-0 ml-1">
        <LogoMark class="w-6 h-6" data-tauri-drag-region />
        <span data-tauri-drag-region class="display text-[19px] text-ink leading-none">WireSpot</span>
      </div>

      <!-- Tabs: outlined pill with a solid active pill that slides -->
      <nav v-if="!minimal" class="tabs absolute left-1/2 -translate-x-1/2">
        <span class="indicator" :style="{ transform: `translateX(${activeIndex * 100}%)` }"></span>
        <button v-for="(tab, i) in tabs" :key="tab.id" class="tab" :class="{ active: page === tab.id }"
          :data-tip="`Ctrl+${i + 1}`" data-tip-pos="bottom" @click="setPage(tab.id)">
          {{ tab.label }}
          <span v-if="tab.id === 'devices' && waiting" class="badge" aria-label="Devices waiting"></span>
        </button>
      </nav>

      <div v-if="!minimal" data-tauri-drag-region class="ml-auto flex items-center gap-2">
        <Transition name="pop">
          <button v-if="waiting" class="chip waiting" data-tip="Devices waiting for your approval" data-tip-pos="bottom"
            @click="setPage('devices')">
            <span class="wait-dot !w-1.5 !h-1.5"></span>
            {{ waiting }} waiting
          </button>
        </Transition>
        <span :class="state === 'live' ? 'tag' : 'chip'" class="state" :data-state="state" :data-tip="stateTip"
          data-tip-pos="bottom">
          <span v-if="state === 'live'" class="live-dot !bg-on-accent !w-1.5 !h-1.5"></span>
          <span v-else-if="state === 'busy' || state === 'unknown'" class="spinner !w-2.5 !h-2.5 !border-[1.5px]"></span>
          <span v-else class="w-1.5 h-1.5 rounded-full dot"></span>
          {{ stateLabel }}
        </span>
      </div>
    </header>

    <Transition name="rise">
      <div v-if="down" class="relative z-20 shrink-0 px-4 py-2 text-xs flex items-center gap-2 bg-warn-soft text-warn border-b border-line">
        <span class="spinner !w-3 !h-3 !border-[1.5px]"></span>
        <span class="font-semibold">The WireSpot engine stopped.</span>
        <span class="truncate opacity-80">{{ down }}</span>
        <span class="ml-auto shrink-0">Restarting… your VPN and hotspot are not affected.</span>
      </div>
    </Transition>

    <main class="relative z-10 flex-1 min-h-0">
      <slot></slot>
    </main>

    <ToastHost />
    <TooltipLayer />
  </div>
</template>

<style scoped>
.light {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  box-shadow: inset 0 0 0 0.5px rgba(0, 0, 0, 0.25);
  transition: background-color 150ms ease, transform 150ms ease;
}

.light svg {
  width: 8px;
  height: 8px;
  fill: none;
  stroke: rgba(40, 10, 10, 0.7);
  stroke-width: 1.4;
  stroke-linecap: round;
  opacity: 0;
  transition: opacity 120ms ease;
}

.lights:hover .light svg {
  opacity: 1;
}

.light:active:not(:disabled) {
  transform: scale(0.88);
}

.close {
  background: #ff5f57;
}

.min {
  background: #febc2e;
}

.zoom {
  background: var(--line-strong);
}

.lights.blurred .light {
  background: var(--line-strong);
}

.tabs {
  display: flex;
  padding: 3px;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  background: color-mix(in srgb, var(--bg) 60%, transparent);
}

.tab {
  position: relative;
  z-index: 1;
  width: 5.25rem;
  height: 2rem;
  border-radius: 999px;
  font-size: 0.84rem;
  font-weight: 600;
  color: var(--ink-2);
  transition: color 300ms ease;
}

.tab:hover {
  color: var(--ink);
}

.tab.active {
  color: var(--btn-ink);
}

.badge {
  position: absolute;
  top: 5px;
  right: 9px;
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--warn);
  box-shadow: 0 0 0 2px var(--bg);
}

.indicator {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 5.25rem;
  height: 2rem;
  border-radius: 999px;
  background: var(--btn);
  /* a smooth glide with no overshoot, so it never pokes out of the pill */
  transition: transform 480ms var(--ease-quint), background-color 300ms ease;
}

.waiting {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 55%, transparent);
  font-weight: 700;
  transition: background-color 150ms ease, transform 220ms var(--ease-spring);
}

.waiting:hover {
  background: var(--warn-soft);
}

.waiting:active {
  transform: scale(0.94);
}

.state .dot {
  background: var(--faint);
}

.state[data-state="vpn"] .dot {
  background: var(--ok);
}

.state[data-state="paused"] .dot {
  background: var(--warn);
}

.state[data-state="error"] {
  color: var(--danger);
  border-color: color-mix(in srgb, var(--danger) 55%, transparent);
}

.state[data-state="error"] .dot {
  background: var(--danger);
}
</style>
