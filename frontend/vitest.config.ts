import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    // Pin the time zone so date formatting assertions match CI on every machine.
    env: { TZ: "UTC" },
    setupFiles: ["./src/test/setup.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      // api.ts imports "server-only", which only resolves inside the Next.js server build.
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/types/**", "src/lib/api.ts"],
      reporter: ["text-summary", "json-summary"],
    },
  },
});
