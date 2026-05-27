import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 650,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/recharts")) return "recharts_vendor";
          if (id.includes("node_modules/d3-")) return "d3_vendor";
          if (id.includes("node_modules/lodash")) return "lodash_vendor";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/analytics": "http://127.0.0.1:8000",
      "/ml": "http://127.0.0.1:8000",
      "/agent": "http://127.0.0.1:8000",
      "/ingestion": "http://127.0.0.1:8000",
      "/plans": "http://127.0.0.1:8000",
      "/territories": "http://127.0.0.1:8000",
      "/reports": "http://127.0.0.1:8000",
      "/grading": "http://127.0.0.1:8000",
      "/data-quality": "http://127.0.0.1:8000",
      "/payout": "http://127.0.0.1:8000",
      "/forecasting": "http://127.0.0.1:8000",
    }
  }
});
