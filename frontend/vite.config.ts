import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  build: {
    // Cytoscape alone is ~570 kB minified; that is the floor for a real graph
    // renderer, so the default 500 kB warning has nothing left to tell us.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      // Cytoscape and its layout extension are ~70% of the bundle and change
      // far less often than the app, so they get their own cacheable chunk.
      output: {
        manualChunks: { cytoscape: ["cytoscape", "cytoscape-fcose"] },
      },
    },
  },
});
