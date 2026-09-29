import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// The pronunciation workbench, compiled to one HTML file that
// `authorlm workbench` serves from authorlm/workbench_dist/.
export default defineConfig({
  base: "./",
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: "../../authorlm/workbench_dist",
    emptyOutDir: true,
    cssCodeSplit: false,
  },
});
