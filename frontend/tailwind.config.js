import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import plugin from 'tailwindcss/plugin'

// The design system is WorkChord's DESIGN.md. Its frontmatter tokens live in
// workchord-tokens.json (regenerate with scripts/design-tokens.mjs); values
// that DESIGN.md states only in prose live in workchord-prose.json.
// Next to this file, or under the working directory when a test runner rewrites module URLs.
const themeFile = (name) =>
  [fileURLToPath(new URL(`./src/theme/${name}`, import.meta.url)), join(process.cwd(), 'src', 'theme', name)].find(
    (path) => existsSync(path),
  )
const read = (name) => JSON.parse(readFileSync(themeFile(name), 'utf8'))
const tokens = read('workchord-tokens.json')
const prose = read('workchord-prose.json')

// "rgb(37 99 235)" becomes "rgb(37 99 235 / <alpha-value>)" so opacity modifiers work.
const withAlpha = (value) => value.replace(/^rgb\((\d+) (\d+) (\d+)\)$/, 'rgb($1 $2 $3 / <alpha-value>)')
const colors = Object.fromEntries(
  Object.entries(tokens.colors).map(([name, value]) => [name, withAlpha(value)]),
)

const typeStep = (style) => [
  style.fontSize,
  {
    lineHeight: String(style.lineHeight),
    fontWeight: String(style.fontWeight),
    ...(style.letterSpacing ? { letterSpacing: style.letterSpacing } : {}),
  },
]

/** @type {import('tailwindcss').Config} */
export default {
  // 7. Hover styles apply only where the device can hover, so a tap never leaves one stuck.
  future: { hoverOnlyWhenSupported: true },
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    // Only the system's own colours exist, so nothing off-palette can be used by accident.
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      inherit: 'inherit',
      ...colors,
      // Fixed-opacity roles DESIGN.md names without a value; each is cited in workchord-prose.json.
      ...Object.fromEntries(Object.entries(prose.derived.colors).map(([name, role]) => [name, role.value])),
    },
    screens: {
      // DESIGN.md: gutters shrink at 520px and below; the sidebar becomes a drawer at 1180px and below.
      roomy: `${parseInt(prose.layout.phoneBelow, 10) + 1}px`,
      wide: `${parseInt(prose.layout.drawerBelow, 10) + 1}px`,
    },
    fontFamily: {
      sans: tokens.typography.body.fontFamily.split(',').map((face) => face.trim()),
      mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
    },
    borderRadius: { none: '0', ...tokens.rounded, full: '9999px' },
    boxShadow: {
      none: 'none',
      resting: prose.shadows.resting,
      raised: prose.shadows.raised,
      popover: prose.shadows.popover,
    },
    extend: {
      fontSize: {
        ...Object.fromEntries(Object.entries(tokens.typography).map(([name, style]) => [name, typeStep(style)])),
        metadata: [prose.type.metadata, { lineHeight: '1.25' }],
        counter: [prose.type.counter, { lineHeight: '1.25' }],
      },
      spacing: {
        ...tokens.spacing,
        conductor: prose.layout.conductorHeight,
        sidebar: prose.layout.sidebarWidth,
        control: tokens.components['button-primary'].height,
        'control-lg': prose.controls.large,
        'control-sm': prose.controls.small,
        'workspace-item': tokens.components['workspace-active'].height,
        'phone-x': prose.layout.phoneGutterX,
        touch: prose.layout.coarseTarget,
      },
      maxWidth: { page: prose.layout.pageMaxWidth },
      transitionDuration: { state: prose.derived.stateDuration.value },
      keyframes: {
        // Starts already visible, so a delayed or paused animation never hides the drawer.
        'drawer-in': {
          from: { transform: 'translateX(-16px)', opacity: '0.85' },
          to: { transform: 'translateX(0)', opacity: '1' },
        },
      },
    },
  },
  plugins: [
    // DESIGN.md: coarse-pointer controls get at least 44px targets.
    plugin(({ addVariant }) => addVariant('pointer-coarse', '@media (pointer: coarse)')),
  ],
}
