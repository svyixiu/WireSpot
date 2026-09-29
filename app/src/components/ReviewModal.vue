<script setup lang="ts">
import { computed, ref } from 'vue';
import { useEngine } from '@/composables/engine';
import { useDialogs } from '@/composables/dialogs';
import { useToasts } from '@/composables/toasts';
import { errorText, kvLabel } from '@/utils/format';
import BaseModal from './BaseModal.vue';

// A WireGuard .conf about to be imported: picked by you, or noticed in
// Downloads. The engine checked it; the private key is never shown.
const engine = useEngine();
const { reviews } = useDialogs();
const { toast } = useToasts();
const review = computed(() => reviews.value[0] ?? null);
const fromDownloads = computed(() => review.value?.origin !== 'import');
const busy = ref(false);

const title = computed(() => {
    const r = review.value;
    if (!r) return '';
    if (r.duplicate) return 'Already imported.';
    if (r.error) return "This file can't be used.";
    return fromDownloads.value ? 'A new VPN profile.' : 'Import this profile?';
});

async function choose(choice: 'move' | 'copy' | 'decline' | null) {
    const r = review.value;
    if (!r || busy.value) return;
    busy.value = true;
    try {
        await engine.call('review', { id: r.id, choice });
    } catch (e) {
        toast('error', "Couldn't import", errorText(e));
    } finally {
        busy.value = false;
        reviews.value = reviews.value.slice(1);
    }
}
</script>

<template>
    <BaseModal :open="!!review" :eyebrow="fromDownloads ? 'Found in Downloads' : 'Import'" :title="title" width="36rem" @close="choose(null)">
        <template v-if="review">
            <p class="text-[15px] text-ink-2 leading-snug">
                <span class="font-mono text-[13px] text-ink font-semibold break-all">{{ review.file }}</span>
                <template v-if="review.duplicate"> is the same profile as <span class="font-mono text-[13px]">{{ review.duplicate }}</span>, which you already have.</template>
                <template v-else-if="review.error"> — {{ review.error }}</template>
            </p>

            <div v-if="review.details" class="mt-4 rounded-2xl border border-line px-4 py-2">
                <div v-for="[k, v] in review.details" :key="k" class="kv border-b border-line last:border-b-0">
                    <span>{{ kvLabel(k) }}</span>
                    <span :class="k.includes('key') ? 'font-mono text-[12px]' : ''">{{ v }}</span>
                </div>
            </div>
            <p v-if="review.details" class="text-xs text-muted mt-3 leading-relaxed">
                Moving is safer: the private key then exists once, in WireSpot's VPN folder, instead of also sitting in the
                original location.
            </p>
        </template>
        <template #footer>
            <template v-if="review?.details">
                <button class="btn btn-link mr-auto" :disabled="busy" @click="choose(fromDownloads ? 'decline' : null)">
                    {{ fromDownloads ? "Don't import" : 'Cancel' }}
                </button>
                <button class="btn btn-glass" :disabled="busy" @click="choose('copy')">Copy</button>
                <button class="btn btn-primary" :disabled="busy" @click="choose('move')">Move into WireSpot</button>
            </template>
            <template v-else>
                <button v-if="fromDownloads" class="btn btn-glass" :disabled="busy" @click="choose('decline')">Don't offer again</button>
                <button class="btn btn-primary" :disabled="busy" @click="choose(null)">OK</button>
            </template>
        </template>
    </BaseModal>
</template>
