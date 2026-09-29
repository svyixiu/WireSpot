import { createGlobalState } from '@vueuse/core'
import { ref } from 'vue'

/** A .conf file waiting to be imported (bridge.py _review_view). Never includes the private key. */
export interface Review {
    id: number;
    file: string;
    origin: string;
    duplicate?: string;
    error?: string;
    details?: [string, string][];
}

/** A decision the engine needs while going live (Controller.decide). */
export interface Ask {
    id: number;
    question: string;
    default: number;
    choices: { key: string; label: string; hint: string }[];
}

/** The dialogs App.vue shows over every page. */
export const useDialogs = createGlobalState(() => {
    const reviews = ref<Review[]>([])
    const asks = ref<Ask[]>([])
    const uninstallOpen = ref(false)
    const changelogOpen = ref(false)
    return { reviews, asks, uninstallOpen, changelogOpen }
})
