<script setup lang="ts">
import { ref, watch } from 'vue';
import { useEngine } from '@/composables/engine';
import { useDialogs } from '@/composables/dialogs';
import { useToasts } from '@/composables/toasts';
import { errorText } from '@/utils/format';
import BaseModal from './BaseModal.vue';

const engine = useEngine();
const { uninstallOpen } = useDialogs();
const { toast } = useToasts();
const keep = ref(true);
const running = ref(false);

watch(uninstallOpen, open => { if (open) { keep.value = true; running.value = false; } });

async function go() {
    running.value = true;
    try {
        await engine.call('uninstall', { keep: keep.value });
        // the engine reports back with an "uninstalled" event (App.vue), or a toast if something is left
        setTimeout(() => { running.value = false; }, 60_000);
    } catch (e) {
        running.value = false;
        toast('error', "Couldn't uninstall", errorText(e));
    }
}
</script>

<template>
    <BaseModal :open="uninstallOpen" eyebrow="Remove" title="Uninstall WireSpot?" width="32rem" :persistent="running"
        @close="uninstallOpen = false">
        <p class="text-[15px] text-ink-2 leading-snug">
            WireSpot first stops its own VPN tunnel, hotspot and DNS lock, then removes the app, its shortcuts and Start with
            Windows.
        </p>
        <div class="mt-4 space-y-2">
            <button class="choice" :class="{ on: keep }" :disabled="running" @click="keep = true">
                <span class="text-[15px] font-semibold text-ink">Keep my settings and VPN profiles</span>
                <span class="text-sm text-muted">%APPDATA%\WireSpot stays, for a later reinstall.</span>
            </button>
            <button class="choice" :class="{ on: !keep }" :disabled="running" @click="keep = false">
                <span class="text-[15px] font-semibold text-ink">Delete everything</span>
                <span class="text-sm text-muted">Also deletes your .conf profiles and their private keys. This can't be undone.</span>
            </button>
        </div>
        <template #footer>
            <button class="btn btn-glass" :disabled="running" @click="uninstallOpen = false">Cancel</button>
            <button class="btn btn-danger" :disabled="running" @click="go">
                <span v-if="running" class="spinner"></span>
                {{ running ? 'Uninstalling…' : 'Uninstall' }}
            </button>
        </template>
    </BaseModal>
</template>
