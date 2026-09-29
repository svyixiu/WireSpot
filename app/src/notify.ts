// The tray pop-up window: devices waiting for approval and the engine's
// messages while the main window is hidden (see src-tauri/src/notify.rs).
import { createApp } from 'vue'
import '@fontsource-variable/archivo'
import '@/theme/style.css'
import '@/theme/app.css'
import { applyAppearance } from '@/theme/themes'
import { readSavedAppearance } from '@/composables/settings'
import NotifyApp from './NotifyApp.vue'

const saved = readSavedAppearance()
applyAppearance(saved.theme, saved.accent, saved.reduceMotion, saved.custom)

createApp(NotifyApp).mount('#notify')
