<script setup lang="ts">
import { computed } from 'vue'
import { Cat, Package, Bike, Cpu, BatteryCharging, Droplet, Boxes } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import { useColonyStore } from '@/stores/colony'
import { RESOURCE_LABELS } from '@/utils/format'
import { formatAmount } from '@/utils/format'

const colony = useColonyStore()

const items = computed(() =>
  (
    [
      ['catnip', Cat],
      ['scrap', Package],
      ['chips', Cpu],
      ['alloys', Boxes],
      ['battery', BatteryCharging],
      ['lube', Droplet],
    ] as const
  ).map(([key, icon]) => {
    const cap = colony.caps[key] ?? 0
    const value = colony.resources[key]
    return {
      key,
      label: RESOURCE_LABELS[key],
      icon,
      value,
      cap,
      capped: cap > 0 && value >= cap,
    }
  }),
)

const rateText = computed(() => {
  const rate = colony.catnipRate
  const sign = rate >= 0 ? '+' : ''
  return `净 ${sign}${rate.toFixed(2)}/s`
})
</script>

<template>
  <header class="panel relative flex items-center gap-4 overflow-hidden px-3 py-2">
    <div class="pointer-events-none absolute inset-y-0 w-1/3 animate-scan bg-gradient-to-r from-transparent via-terminal-accent/5 to-transparent" />

    <div class="flex items-center gap-1 text-terminal-accent">
      <Bike class="h-4 w-4" />
      <span class="text-[12px] tracking-wide">07 号避难所</span>
    </div>

    <div
      v-for="item in items"
      :key="item.key"
      class="flex items-center gap-1.5"
      :class="item.capped ? 'text-terminal-warn' : ''"
    >
      <component :is="item.icon" class="h-3.5 w-3.5 text-terminal-dim" />
      <span class="stat-label">{{ item.label }}</span>
      <span class="tabular-nums">{{ formatAmount(item.value) }}</span>
      <span class="text-[10px] text-terminal-dim">/ {{ item.cap }}</span>
      <Badge v-if="item.capped" text="爆仓" tone="warn" />
    </div>

    <div class="ml-auto flex items-center gap-3">
      <span class="stat-label">{{ rateText }}</span>
      <template v-if="colony.isStarving">
        <Badge text="低血糖瘫软罢工" tone="crit" />
      </template>
    </div>
  </header>
</template>
