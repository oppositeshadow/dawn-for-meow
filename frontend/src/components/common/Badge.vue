<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    text: string
    tone?: 'dim' | 'accent' | 'warn' | 'crit'
  }>(),
  { tone: 'dim' },
)

const toneClass = computed(
  () =>
    ({
      dim: 'border-terminal-line text-terminal-dim',
      accent: 'border-terminal-accent/50 text-terminal-accent',
      warn: 'border-terminal-warn/50 text-terminal-warn',
      crit: 'border-terminal-crit/60 text-terminal-crit',
    })[props.tone],
)
</script>

<template>
  <span class="rounded border px-1.5 py-0.5 text-[11px] leading-none" :class="toneClass">
    {{ text }}
  </span>
</template>
