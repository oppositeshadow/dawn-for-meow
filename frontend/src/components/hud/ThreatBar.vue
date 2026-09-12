<script setup lang="ts">
import { computed } from 'vue'
import { Zap, Radar, Users } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import GaugeBar from '@/components/common/GaugeBar.vue'
import { useColonyStore } from '@/stores/colony'

const colony = useColonyStore()

const suspicionPercent = computed(() => Math.round(colony.suspicion.current))
const suspicionTone = computed<'accent' | 'warn' | 'crit'>(() => {
  if (suspicionPercent.value >= 75) return 'crit'
  if (suspicionPercent.value >= 40) return 'warn'
  return 'accent'
})
</script>

<template>
  <div class="panel flex items-center gap-6 px-3 py-2">
    <div class="flex min-w-[190px] items-center gap-2">
      <Zap class="h-3.5 w-3.5 text-terminal-dim" />
      <span class="stat-label">净电力</span>
      <span class="tabular-nums" :class="colony.power.blackout ? 'text-terminal-crit' : ''">
        {{ colony.power.net_kw >= 0 ? '+' : '' }}{{ colony.power.net_kw.toFixed(0) }} kW
      </span>
      <span class="text-[10px] text-terminal-dim">
        电容 {{ colony.power.battery_kwh.toFixed(0) }}/{{ colony.power.battery_kwh_max.toFixed(0) }} kWh
      </span>
      <Badge v-if="colony.power.blackout" text="电网欠载" tone="crit" />
    </div>

    <div class="flex flex-1 items-center gap-2">
      <Radar
        class="h-3.5 w-3.5 text-terminal-dim"
        :class="suspicionPercent >= 75 ? 'animate-pulse-warn text-terminal-crit' : ''"
      />
      <span class="stat-label">天网警戒度</span>
      <div class="w-40">
        <GaugeBar :value="suspicionPercent" :max="100" :tone="suspicionTone" />
      </div>
      <span class="tabular-nums">{{ suspicionPercent }}%</span>
      <span class="text-[10px] text-terminal-dim">
        实值 {{ colony.localSuspicion.toFixed(2) }} / 100
      </span>
    </div>

    <div class="flex items-center gap-2">
      <Users class="h-3.5 w-3.5 text-terminal-dim" />
      <span class="stat-label">猫口</span>
      <span class="tabular-nums">{{ colony.population.total }}/{{ colony.population.max_cap }}</span>
      <Badge v-if="colony.population.max_cap === 0" text="无窝" tone="warn" />
      <span class="text-[10px] text-terminal-dim">空闲 {{ colony.population.unassigned }}</span>
    </div>
  </div>
</template>
