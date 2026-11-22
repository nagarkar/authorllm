import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// The audiobook review page, compiled to one HTML file that
// `authorlm audio serve` serves from authorlm/audiobook_dist/.
export default defineConfig({
  base: "./",
  // The brand icons (authorlm/brand/, copied from metalm) are served by the
  // Python server at the site root; the dev server serves them from here too,
  // and the build leaves them out of the dist (the page links them by url).
  publicDir: "../../authorlm/brand",
  // A stamp per build: the open page compares it with the served page's and
  // offers a reload when they differ (2026-11-21: a phone kept an old copy).
  define: { __BUILD__: JSON.stringify(`build-${Date.now()}`) },
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: "../../authorlm/audiobook_dist",
    emptyOutDir: true,
    cssCodeSplit: false,
    copyPublicDir: false,
  },
});
