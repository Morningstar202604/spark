/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        ink: "var(--ink)",
        "ink-muted": "var(--ink-muted)",
        accent: "var(--accent)",
      },
      borderRadius: {
        xl2: "18px",
      },
      boxShadow: {
        panel: "0 16px 40px rgba(2, 6, 23, 0.08)",
      },
    },
  },
  plugins: [],
};
