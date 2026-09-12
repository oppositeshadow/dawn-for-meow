/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        // 低干扰黑白灰 + 一点冰蓝（不刺眼、不花哨）
        terminal: {
          bg: '#0b0d10',
          panel: '#12151a',
          line: '#1f242c',
          dim: '#6b7280',
          text: '#d7dce3',
          accent: '#7dd3fc',
          warn: '#f5c97b',
          crit: '#f08a8a',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Consolas', 'ui-monospace', 'monospace'],
      },
      keyframes: {
        glow: {
          '0%, 100%': { boxShadow: '0 0 0 rgba(125,211,252,0)' },
          '50%': { boxShadow: '0 0 6px rgba(125,211,252,0.35)' },
        },
        scan: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
        pulseWarn: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.55' },
        },
      },
      animation: {
        glow: 'glow 2.4s ease-in-out infinite',
        scan: 'scan 6s linear infinite',
        'pulse-warn': 'pulseWarn 1.2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
