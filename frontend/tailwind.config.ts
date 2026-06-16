import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#111827",
        panel: "#ffffff",
        line: "#d7dde5",
        positive: "#047857",
        warning: "#b45309",
        danger: "#b91c1c",
      },
    },
  },
  plugins: [],
};

export default config;
