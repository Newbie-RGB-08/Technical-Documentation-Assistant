import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { buildProxyConfig } from "./config/proxy";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: buildProxyConfig(process.env),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: false,
  },
});
