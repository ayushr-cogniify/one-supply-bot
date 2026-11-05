import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// BASE is the GitHub Pages path, e.g. /one-supply-bot/ for https://<user>.github.io/one-supply-bot/
export default defineConfig({
  base: process.env.BASE ?? "./",
  plugins: [react()],
  worker: { format: "es" },
  build: { target: "es2022", chunkSizeWarningLimit: 900 },
});
