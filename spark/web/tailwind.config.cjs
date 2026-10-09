/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
  ],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        ink: "var(--ink)",
        "ink-muted": "var(--ink-muted)",
        accent: "var(--accent)",
        card: "var(--card)",
        border: "var(--border)",
        input: "var(--input)",
        ring: "var(--ring, var(--accent))",
        muted: "var(--muted, var(--surface-2))",
        "muted-foreground": "var(--muted-foreground, var(--ink-muted))",
        secondary: "var(--secondary, var(--surface-2))",
        "secondary-foreground": "var(--secondary-foreground, var(--ink))",
        destructive: "var(--destructive, #d64545)",
        "destructive-foreground": "var(--destructive-foreground, #fff)",
        "accent-foreground": "var(--accent-foreground, #fff)",
        primary: "var(--primary, var(--accent))",
        background: "var(--background, var(--surface))",
      },
      borderRadius: {
        xl2: "18px",
        lg: "var(--radius, 0.5rem)",
        md: "calc(var(--radius, 0.5rem) - 2px)",
        sm: "calc(var(--radius, 0.5rem) - 4px)",
      },
      boxShadow: {
        panel: "0 16px 40px rgba(2, 6, 23, 0.08)",
      },
    },
  },
  plugins: [],
};
