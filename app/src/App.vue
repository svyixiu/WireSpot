<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import { until } from '@vueuse/core';
import { invoke } from '@tauri-apps/api/core';
import MainLayout from './components/MainLayout.vue';
import { inDesktopApp, useEngine } from './composables/engine';
import { useNav } from './composables/nav';
import { useToasts } from './composables/toasts';
import { useDialogs, type Ask, type Review } from './composables/dialogs';
import { useShell } from './composables/shell';
import { useBrandIcon } from './theme/brand-icon';
import InstallerView from './pages/InstallerView.vue';
import HomeView from './pages/HomeView.vue';
import DevicesView from './pages/DevicesView.vue';
import ProfilesView from './pages/ProfilesView.vue';
import HotspotView from './pages/HotspotView.vue';
import ChecksView from './pages/ChecksView.vue';
import ActivityView from './pages/ActivityView.vue';
import SettingsView from './pages/SettingsView.vue';
import AskModal from './components/AskModal.vue';
import ReviewModal from './components/ReviewModal.vue';
import UninstallModal from './components/UninstallModal.vue';
import TermsModal from './components/TermsModal.vue';
import ChangelogModal from './components/ChangelogModal.vue';

const engine = useEngine();
const { page, setPage } = useNav();
const { toast } = useToasts();
const dialogs = useDialogs();
// the same WireSpot.exe is the installer and the app (see composables/shell.ts)
const shell = useShell();
const { mode } = shell;

// the taskbar icon in the accent color; an installed copy's shortcuts follow too
const iconReady = useBrandIcon(() => !!engine.hello.value?.installed);

// ----- what the engine pushes -----
const deviceName = (d: { name?: string; device?: string }) => (d.name && d.name !== '(no name)' ? d.name : d.device || 'A device');
const offs = [
  engine.on('toast', (t: { title: string; body: string; error: boolean }) => toast(t.error ? 'error' : 'info', t.title, t.body, 4200)),
  engine.on('notify', (n: { kind: string; message: string }) => toast(n.kind === 'error' ? 'error' : 'info', n.message)),
  engine.on('pending', (d: { name: string; device: string; ip: string }) =>
    toast('warning', `${deviceName(d)} wants to join`, 'Allow or block it on Home or in Devices.', 6000)),
  engine.on('navigate', (n: { page: string }) => setPage(n.page)),
  engine.on('ask', (a: Ask) => { dialogs.asks.value = [...dialogs.asks.value, a]; }),
  engine.on('review', (r: Review) => { dialogs.reviews.value = [...dialogs.reviews.value, r]; }),
  engine.on('uninstall_prompt', () => { setPage('settings'); dialogs.uninstallOpen.value = true; }),
  engine.on('uninstalled', (u: { message: string }) => {
    dialogs.uninstallOpen.value = false;
    toast('success', 'WireSpot was uninstalled', u.message, 4000);
    setTimeout(() => invoke('quit_app').catch(() => {}), 2500);
  }),
  // the desktop app copies on the Rust side; the browser preview does it here
  engine.on('clipboard', (c: { text: string }) => { navigator.clipboard?.writeText(c.text).catch(() => {}); }),
];
onUnmounted(() => offs.forEach(off => off()));

// ----- startup splash: report loading stages (each turns a dot green) -----
const stage = (n: number) => invoke('splash_stage', { stage: n }).catch(() => {});
const within = <T,>(promise: Promise<T>, ms: number) =>
  Promise.race([promise.catch(() => undefined), new Promise(resolve => setTimeout(resolve, ms))]);

onMounted(async () => {
  // 1: the interface is up, in the right colors and with the right icon
  await within(iconReady, 1500);
  stage(1);
  // 2: the engine is running
  await engine.ready;
  await within(until(engine.hello).toBeTruthy(), 8000);
  stage(2);
  // 3: it knows the state of the VPN and the hotspot (the installer has nothing to wait for)
  await shell.ready;
  if (mode.value === 'app') await within(until(engine.snap).toBeTruthy(), 8000);
  stage(3);
  // started as `WireSpot.exe --uninstall` (Apps & features)
  if (inDesktopApp && mode.value === 'app') {
    const args = await invoke<string[]>('launch_args').catch(() => [] as string[]);
    if (args.includes('--uninstall')) { setPage('settings'); dialogs.uninstallOpen.value = true; }
  }
});
</script>

<template>
  <MainLayout :minimal="mode !== 'app'">
    <Transition name="page"><InstallerView v-if="mode === 'setup'" class="page" /></Transition>

    <template v-if="mode === 'app'">
    <!-- v-show keeps every page alive, so state survives switching -->
    <Transition name="page"><HomeView v-show="page === 'home'" class="page" /></Transition>
    <Transition name="page"><DevicesView v-show="page === 'devices'" class="page" /></Transition>
    <Transition name="page"><ProfilesView v-show="page === 'profiles'" class="page" /></Transition>
    <Transition name="page"><HotspotView v-show="page === 'hotspot'" class="page" /></Transition>
    <Transition name="page"><ChecksView v-show="page === 'checks'" class="page" /></Transition>
    <Transition name="page"><ActivityView v-show="page === 'activity'" class="page" /></Transition>
    <Transition name="page"><SettingsView v-show="page === 'settings'" class="page" /></Transition>

    <AskModal />
    <ReviewModal />
    <UninstallModal />
    <ChangelogModal :open="dialogs.changelogOpen.value" :current-version="engine.hello.value?.version"
      @close="dialogs.changelogOpen.value = false" />
    <TermsModal />
    </template>
  </MainLayout>
</template>

<style>
.page {
  position: absolute;
  inset: 0;
}

.page-enter-active {
  transition: opacity 320ms ease, transform 600ms var(--ease-quint), filter 320ms ease;
}

.page-leave-active {
  transition: opacity 160ms ease, filter 160ms ease;
}

.page-enter-from {
  opacity: 0;
  transform: translateY(12px) scale(0.99);
  filter: blur(6px);
}

.page-leave-to {
  opacity: 0;
  filter: blur(4px);
}
</style>
