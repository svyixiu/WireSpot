import path from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST

export default defineConfig(async () => ({
    plugins: [vue(), tailwindcss()],
    resolve: {
        alias: {
            '@/': `${path.resolve(__dirname, 'src')}/`,
        },
    },
    // three pages: the app, the startup splash and the tray pop-ups (each its own window)
    build: {
        rollupOptions: {
            input: {
                main: path.resolve(__dirname, 'index.html'),
                splash: path.resolve(__dirname, 'splash.html'),
                notify: path.resolve(__dirname, 'notify.html'),
            },
        },
    },
    // Tauri expects a fixed port and its own error output
    clearScreen: false,
    server: {
        port: 1430,
        strictPort: true,
        host: host || false,
        hmr: host ? { protocol: 'ws', host, port: 1431 } : undefined,
        watch: { ignored: ['**/src-tauri/**'] },
    },
}))
