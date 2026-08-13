import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      colors: {
        background: "#F8FAFC",
        surface: "#FFFFFF",
        sidebar: {
          DEFAULT: "#111827",
          hover: "#1F2937",
          border: "#1F2937",
          text: "#CBD5E1",
          "text-muted": "#64748B",
        },
        border: {
          DEFAULT: "#E2E8F0",
          strong: "#CBD5E1",
        },
        ink: {
          DEFAULT: "#111827",
          muted: "#64748B",
          subtle: "#94A3B8",
        },
        accent: {
          DEFAULT: "#2563EB",
          hover: "#1D4ED8",
          subtle: "#EFF6FF",
          border: "#BFDBFE",
        },
        success: {
          DEFAULT: "#16A34A",
          subtle: "#F0FDF4",
          border: "#BBF7D0",
        },
        warning: {
          DEFAULT: "#D97706",
          subtle: "#FFFBEB",
          border: "#FDE68A",
        },
        critical: {
          DEFAULT: "#DC2626",
          subtle: "#FEF2F2",
          border: "#FECACA",
        },
        info: {
          DEFAULT: "#0891B2",
          subtle: "#ECFEFF",
          border: "#A5F3FC",
        },
      },
      boxShadow: {
        subtle: "0 1px 2px 0 rgb(15 23 42 / 0.04)",
        panel: "0 1px 3px 0 rgb(15 23 42 / 0.06), 0 1px 2px -1px rgb(15 23 42 / 0.06)",
      },
      fontSize: {
        xs: ["0.75rem", { lineHeight: "1rem" }],
        sm: ["0.8125rem", { lineHeight: "1.25rem" }],
        base: ["0.875rem", { lineHeight: "1.375rem" }],
      },
    },
  },
  plugins: [],
} satisfies Config;
