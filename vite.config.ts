import { defineConfig } from 'vite';

// https://vitejs.dev/config/
// Tauri uses Vite as the frontend build tool.
// The server port must match "devUrl" in tauri.conf.json.
export default defineConfig(async () => ({
  // Prevent Vite from obscuring Rust errors.
  clearScreen: false,

  server: {
    port: 5173,
    // Let Tauri open the webview on the correct port.
    strictPort: true,
    // Tauri expects the Vite dev server to listen on all interfaces
    // in some configurations, but localhost is fine for desktop-only.
    host: 'localhost',
    watch: {
      // Tell Vite to watch the Rust source so it can reload on backend change.
      ignored: ['**/src-tauri/**'],
    },
  },

  // Vite env var prefix exposed to the frontend via import.meta.env
  envPrefix: ['VITE_', 'TAURI_'],

  build: {
    // Tauri supports ES2021+ on desktop.
    target: ['es2021', 'safari13'],
    // Disable minification for easier debugging in development.
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
}));
