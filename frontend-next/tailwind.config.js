/** @type {import("tailwindcss").Config} */
module.exports = {
  content: [
    "./app/**/*.{js,jsx,ts,tsx}",
    "./lib/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface)",
        "surface-sunk": "var(--surface-sunk)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        "text-main": "var(--text-main)",
        "text-muted": "var(--text-muted)",
        "text-subtle": "var(--text-subtle)",
        accent: "var(--accent)",
        "accent-soft": "var(--accent-soft)",
        danger: "var(--danger)",
        "danger-soft": "var(--danger-soft)",
        success: "var(--success)",
        "success-soft": "var(--success-soft)",
        info: "var(--info)",
        "info-soft": "var(--info-soft)",
        "neutral-soft": "var(--neutral-soft)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
    },
  },
  plugins: [],
};
