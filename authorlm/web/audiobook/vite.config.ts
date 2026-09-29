import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// The audiobook review page, compiled to one HTML file that
// `authorlm audio serve` serves from authorlm/audiobook_dist/.
export default defineConfig({
  base: "./",
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: "../../authorlm/audiobook_dist",
    emptyOutDir: true,
    cssCodeSplit: false,
  },
});
