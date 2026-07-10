import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#f3f5f7",
        panel: "#ffffff",
        subtle: "#f7f8fa",
        ink: "#101828",
        secondary: "#344054",
        muted: "#667085",
        faint: "#98a2b3",
        line: "#e1e5ea",
        "line-strong": "#cdd3dc",
        brand: "#2563eb",
        "brand-hover": "#1d4ed8",
        "brand-soft": "#eff6ff",
        positive: "#067647",
        "positive-soft": "#ecfdf3",
        warning: "#b54708",
        "warning-soft": "#fffaeb",
        danger: "#b42318",
        "danger-soft": "#fef3f2",
        sidebar: "#0b1220",
        "sidebar-muted": "#94a3b8",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        mono: ["SFMono-Regular", "Cascadia Code", "Roboto Mono", "Consolas", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 2px rgba(16, 24, 40, 0.04)",
        raised: "0 12px 28px rgba(16, 24, 40, 0.12)",
      },
    },
  },
  plugins: [],
};

export default config;
