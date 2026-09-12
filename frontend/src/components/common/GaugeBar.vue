<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    value: number
    max: number
    tone?: 'accent' | 'warn' | 'crit'
    height?: number
  }>(),
  { tone: 'accent', height: 6 },
)

const percent = computed(() => {
  if (!props.max || props.max <= 0) return 0
  return Math.max(0, Math.min(100, (props.value / props.max) * 100))
})

const barClass = computed(
  () =>
    ({
      accent: 'bg-terminal-accent',
      warn: 'bg-terminal-warn',
      crit: 'bg-terminal-crit',
    })[props.tone],
)
</script>

<template>
  <div
    class="w-full overflow-hidden rounded-sm bg-terminal-line/60"
    :style="{ height: `${height}px` }"
  >
    <div class="h-full transition-[width] duration-200 ease-linear" :class="barClass" :style="{ width: `${percent}%` }" />
  </div>
</template>
