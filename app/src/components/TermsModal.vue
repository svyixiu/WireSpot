<script setup lang="ts">
import { computed, ref } from 'vue';
import { invoke } from '@tauri-apps/api/core';
import { useEngine, inDesktopApp } from '@/composables/engine';
import { useSettings } from '@/composables/settings';
import { LINKS, openLink } from '@/data/links';
import BaseModal from './BaseModal.vue';
import AnimatedCheckbox from './AnimatedCheckbox.vue';

// Before first use, and again whenever the Terms change (hello.terms_version).
const { hello } = useEngine();
const { settings, recordAgreement } = useSettings();
const agreed = ref(false);

const version = computed(() => hello.value?.terms_version ?? null);
const open = computed(() => !!version.value && settings.value.termsVersion !== version.value);
const updated = computed(() => !!settings.value.termsVersion);

function accept() {
    if (agreed.value && version.value) recordAgreement(version.value);
}

function quit() {
    if (inDesktopApp) invoke('quit_app').catch(() => {});
}
</script>

<template>
    <BaseModal :open="open" :eyebrow="updated ? 'The Terms changed' : 'Before you start'"
        :title="updated ? 'Please agree to the new Terms.' : 'WireSpot changes how Windows networks.'" width="36rem" persistent>
        <div class="space-y-2.5 text-[14px] text-ink-2 leading-snug">
            <p>
                WireSpot runs as administrator. While it's live it starts a VPN tunnel, turns on the Mobile Hotspot, changes
                routes and locks DNS, and undoes all of it when you disconnect.
            </p>
            <p>
                You're responsible for using it lawfully and for checking the rules of your internet provider, VPN provider,
                school or employer before sharing a connection. Device approval is a local control, not a guarantee: a new
                device can have about a second of access before it's blocked.
            </p>
            <p class="text-muted text-[13px]">
                No accounts, telemetry or analytics. Your VPN keys, settings and device list stay on this PC.
            </p>
        </div>

        <label class="agree mt-5" @click.prevent="agreed = !agreed">
            <AnimatedCheckbox :checked="agreed" label="I agree to the Terms of Use" @toggle="agreed = !agreed" @click.stop />
            <span class="text-[14px] text-ink leading-snug">
                I agree to the
                <button class="doc-link" @click.stop="openLink(LINKS.terms)">Terms of Use</button>
                and have read the
                <button class="doc-link" @click.stop="openLink(LINKS.privacy)">Privacy Policy</button>.
            </span>
        </label>

        <template #footer>
            <button v-if="inDesktopApp" class="btn btn-link mr-auto" @click="quit">Quit</button>
            <button class="btn btn-primary" :disabled="!agreed" @click="accept">Continue</button>
        </template>
    </BaseModal>
</template>

<style scoped>
.agree {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    border-radius: 16px;
    border: 1px solid var(--line-strong);
    cursor: default;
}
</style>
