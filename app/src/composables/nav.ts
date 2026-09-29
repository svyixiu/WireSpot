import { createGlobalState } from '@vueuse/core'
import { ref } from 'vue'

export type Page = 'home' | 'devices' | 'profiles' | 'hotspot' | 'checks' | 'activity' | 'settings'

export const PAGES: Page[] = ['home', 'devices', 'profiles', 'hotspot', 'checks', 'activity', 'settings']

export const useNav = createGlobalState(() => {
    const page = ref<Page>('home')

    function setPage(p: string) {
        page.value = (PAGES as string[]).includes(p) ? p as Page : 'home'
    }

    return { page, setPage }
})
