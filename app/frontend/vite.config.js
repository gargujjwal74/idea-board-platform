import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy /api to a locally running backend.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
