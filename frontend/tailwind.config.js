/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,jsx,ts,tsx}",
    "../packages/receipt-ui/src/**/*.{js,jsx,ts,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Warm-neutral paper ramp (ink on cream → ink on ember).
        primary: {
          50:  "#faf7f2",
          100: "#f2ece0",
          200: "#e6dcc8",
          300: "#d1c3a6",
          400: "#a89878",
          500: "#7d6e53",
          600: "#5a4e3b",
          700: "#3d342a",
          800: "#25201a",
          900: "#17140f",
          950: "#0f0d0c",
          DEFAULT: "#17140f",
        },
        // Amber accent — thermal-receipt ink fade. CSS-var-backed so opacity
        // modifiers (`bg-accent-500/10`) resolve, and a future palette swap
        // lives entirely in tokens.css. Each numeric step is a static literal
        // class (`text-accent-50` … `bg-accent-900`) per JIT requirements.
        accent: {
          50:  "rgb(var(--accent-50)  / <alpha-value>)",
          100: "rgb(var(--accent-100) / <alpha-value>)",
          200: "rgb(var(--accent-200) / <alpha-value>)",
          300: "rgb(var(--accent-300) / <alpha-value>)",
          400: "rgb(var(--accent-400) / <alpha-value>)",
          500: "rgb(var(--accent-500) / <alpha-value>)",
          600: "rgb(var(--accent-600) / <alpha-value>)",
          700: "rgb(var(--accent-700) / <alpha-value>)",
          800: "rgb(var(--accent-800) / <alpha-value>)",
          900: "rgb(var(--accent-900) / <alpha-value>)",
          DEFAULT: "rgb(var(--accent-500) / <alpha-value>)",
        },
        // Semantic aliases — CSS-var backed, swap with .dark on <html>.
        paper:   "rgb(var(--bg-page)    / <alpha-value>)",
        surface: "rgb(var(--bg-surface) / <alpha-value>)",
        sunken:  "rgb(var(--bg-sunken)  / <alpha-value>)",
        rule:    "rgb(var(--rule)       / <alpha-value>)",
        ink: {
          DEFAULT: "rgb(var(--ink-primary) / <alpha-value>)",
          muted:   "rgb(var(--ink-muted)   / <alpha-value>)",
          faint:   "rgb(var(--ink-faint)   / <alpha-value>)",
        },
        // Red-flag badge palette — 5 distinct hues, severity gradient
        // wine → red → orange → amber → honey (most severe first).
        // DEFAULT is now CSS-var-backed (alpha-modifier-safe); bg/fg pairs
        // stay static-hex to keep `bg-flag-secret-bg` literals greppable.
        flag: {
          secret:    { DEFAULT: "rgb(var(--flag-secret)    / <alpha-value>)", bg: "#450a1f", fg: "#ffe4e6" },
          env:       { DEFAULT: "rgb(var(--flag-env)       / <alpha-value>)", bg: "#7f1d1d", fg: "#fee2e2" },
          shell:     { DEFAULT: "rgb(var(--flag-shell)     / <alpha-value>)", bg: "#7c2d12", fg: "#ffedd5" },
          db:        { DEFAULT: "rgb(var(--flag-db)        / <alpha-value>)", bg: "#4a044e", fg: "#f5d0fe" },
          migration: { DEFAULT: "rgb(var(--flag-migration) / <alpha-value>)", bg: "#78350f", fg: "#fef3c7" },
          ci:        { DEFAULT: "rgb(var(--flag-ci)        / <alpha-value>)", bg: "#713f12", fg: "#fef9c3" },
          custom:    { DEFAULT: "rgb(var(--flag-custom)    / <alpha-value>)", bg: "#083344", fg: "#cffafe" },
          skill:     { DEFAULT: "rgb(var(--flag-skill)     / <alpha-value>)", bg: "#1e1b4b", fg: "#e0e7ff" },
        },
        // File-op semantic colors — Swiss "color as information."
        // Use as `text-op-create`, `bg-op-edit/10`, `border-op-delete`.
        op: {
          create: "rgb(var(--op-create) / <alpha-value>)",
          edit:   "rgb(var(--op-edit)   / <alpha-value>)",
          delete: "rgb(var(--op-delete) / <alpha-value>)",
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'ui-monospace', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        micro:   ['0.6875rem', { lineHeight: '1rem',    letterSpacing: '0.02em' }], // 11
        caption: ['0.8125rem', { lineHeight: '1.125rem' }],                         // 13
        // Override Tailwind default 36px → 32px to match DESIGN-SYSTEM §Typography
        // (Swiss page-h1 step). text-2xl (24) and text-lg (18) already correct.
        '4xl':   ['2rem',      { lineHeight: '2.5rem',  letterSpacing: '-0.01em' }], // 32
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        DEFAULT: "var(--radius-md)",
        lg: "var(--radius-lg)",
      },
      boxShadow: {
        // Hairline elevation only — Swiss bans z-stacked Material tiers.
        sm: "var(--shadow-sm)",
        DEFAULT: "var(--shadow-md)",
        md: "var(--shadow-md)",
      },
      transitionDuration: {
        micro: "120ms",
      },
      keyframes: {
        "feed-in": {
          "0%":   { opacity: "0", transform: "translateY(-2px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        // Anchor-scroll flash: ringed amber halo that fades out over 1.6s.
        // Used by SessionDetailPage when navigating to `#event-<id>`.
        "event-flash": {
          "0%":   { backgroundColor: "rgb(var(--accent-500) / 0.20)" },
          "60%":  { backgroundColor: "rgb(var(--accent-500) / 0.15)" },
          "100%": { backgroundColor: "transparent" },
        },
      },
      animation: {
        "feed-in": "feed-in 200ms ease-out",
        "event-flash": "event-flash 1600ms ease-out",
      },
    },
  },
  plugins: [],
}
