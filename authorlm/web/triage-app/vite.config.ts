import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// AK: staleness guard for the inlined tutorial.
//
// TriageApp.tsx inlines docs/writing-essays-tutorial.md at build time via a
// `?raw` import, so the Help tab can never *diverge in content* from the
// doc. But the committed dist (authorlm/triage_dist/index.html) is a build
// product: nothing stops it from going *stale* relative to the doc if
// someone edits the tutorial and forgets to rebuild. HelpTabTest pins the
// title and a few distinctive lines from the doc as tripwires, but a
// tutorial rewrite that happens to preserve those exact lines would still
// pass against a stale dist.
//
// This plugin makes staleness structurally detectable: it hashes the doc's
// raw bytes at BUILD time (Node's `crypto`, not available to the
// browser-bundled app code, which is why this lives here and not in
// TriageApp.tsx or markdown.tsx) and stamps the digest into the emitted
// HTML as `<!-- tutorial-sha256:<hex> -->`. A hermetic Python test then
// hashes the doc itself the same way and asserts the dist carries that
// exact comment — so ANY edit to the doc without a rebuild fails the
// suite, regardless of which lines moved.
//
// Runs as a `generateBundle` hook with `enforce: "post"`, listed after
// `viteSingleFile()`: vite-plugin-singlefile also inlines JS/CSS into the
// HTML chunk's `.source` from its own `enforce: "post"` generateBundle
// hook, and same-phase plugins run in array order, so this plugin sees the
// fully-inlined single-file HTML and appends the checksum comment to it.
function tutorialChecksumPlugin(docPath: string): Plugin {
  return {
    name: "tutorial-checksum",
    enforce: "post",
    generateBundle(_options, bundle) {
      const hash = createHash("sha256")
        .update(readFileSync(docPath))
        .digest("hex");
      for (const fileName of Object.keys(bundle)) {
        if (!fileName.endsWith(".html")) continue;
        const chunk = bundle[fileName];
        if ("source" in chunk && typeof chunk.source === "string") {
          chunk.source = chunk.source.replace(
            "</body>",
            `<!-- tutorial-sha256:${hash} -->\n  </body>`,
          );
        }
      }
    },
  };
}

const tutorialDoc = fileURLToPath(
  new URL("../../docs/writing-essays-tutorial.md", import.meta.url),
);

export default defineConfig({
  base: "./",
  plugins: [react(), viteSingleFile(), tutorialChecksumPlugin(tutorialDoc)],
  build: {
    outDir: "../../authorlm/triage_dist",
    emptyOutDir: true,
    cssCodeSplit: false,
  },
});
