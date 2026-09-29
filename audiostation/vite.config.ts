import { defineConfig } from 'vite';

// Tauri uses Vite as the frontend build tool. The server port must match
// "devUrl" in src-tauri/tauri.conf.json.
export default defineConfig({
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    host: 'localhost',
    watch: { ignored: ['**/src-tauri/**'] },
  },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    target: ['es2021', 'safari13'],
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
});
