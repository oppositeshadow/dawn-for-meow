<script setup lang="ts">
import { computed } from 'vue'
import { Hammer, Pickaxe } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import { useColonyStore } from '@/stores/colony'
import { useFacilitiesStore } from '@/stores/facilities'
import type { FacilityDefinition } from '@/types/game'
import { RESOURCE_LABELS } from '@/utils/format'

const colony = useColonyStore()
const facilitiesStore = useFacilitiesStore()

const list = computed(() =>
  facilitiesStore.facilities.map((definition) => {
    const level = colony.facilities[definition.facility_id] ?? 0
    // 发射井走"阶段式"造价（后端 launch_silo 块），其余设施走递增曲线
    const silo = colony.launchSilo
    const isSilo = definition.facility_id === 'launch_silo'
    const cost = isSilo ? (silo.next_stage?.cost ?? {}) : facilitiesStore.nextCost(definition.facility_id, level)
    const maxed = definition.max_level !== null && level >= definition.max_level
    const buildable = definition.buildable !== false
    const blocked = isSilo && silo.next_stage?.blocked === true
    return {
      definition,
      level,
      cost,
      maxed,
      buildable,
      blocked,
      stageName: isSilo ? silo.next_stage?.name : null,
      affordable: facilitiesStore.affordable(cost, colony.resources),
      effectText: describe(definition),
    }
  }),
)

function describe(definition: FacilityDefinition): string {
  const effects = definition.effects
  const parts: string[] = []
  if (effects.cat_capacity) parts.push(`承载力 K +${effects.cat_capacity}`)
  if (effects.breeding_bonus) parts.push(`繁育 r +${Math.round(effects.breeding_bonus * 100)}%`)
  if (effects.workstation) {
    for (const [jobId, slots] of Object.entries(effects.workstation)) {
      parts.push(`${facilitiesStore.jobName(jobId)}工位 +${slots}`)
    }
  }
  if (effects.power_gen_kw) parts.push(`发电 +${effects.power_gen_kw} kW`)
  if (effects.power_load_kw) parts.push(`耗电 −${effects.power_load_kw} kW`)
  if (effects.noise_multiplier) parts.push('降噪等级加成')
  if (effects.battery_kwh_max) parts.push(`电容池上限 ${effects.battery_kwh_max} kWh`)
  if (effects.unlock_system) parts.push(`解锁 ${effects.unlock_system}`)
  return parts.join(' · ') || definition.description
}

function costText(cost: Record<string, number>): string {
  return Object.entries(cost)
    .map(([resource, amount]) => `${amount} ${RESOURCE_LABELS[resource] ?? resource}`)
    .join(' + ')
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <Hammer class="h-3.5 w-3.5" />
      <span>设施建造</span>
      <Badge :text="`承载力 K=${colony.population.max_cap}`" />
    </div>

    <div class="flex-1 space-y-2 overflow-auto p-3">
      <div
        v-for="item in list"
        :key="item.definition.facility_id"
        class="rounded border border-terminal-line/70 px-2 py-2"
      >
        <div class="flex items-center gap-2">
          <span class="text-[12px]">{{ item.definition.name }}</span>
          <span class="text-[11px] text-terminal-dim">Lv.{{ item.level }}</span>
          <Badge v-if="!item.buildable" text="未定稿" tone="warn" />
          <Badge v-else-if="item.maxed" text="已满级" tone="accent" />
          <Badge v-if="item.blocked" text="需先摧毁除菌要塞" tone="warn" />
          <span v-else-if="item.stageName" class="text-[10px] text-terminal-dim">{{ item.stageName }}</span>
          <button
            class="btn ml-auto"
            :disabled="colony.busy || !item.buildable || item.maxed || item.blocked || !item.affordable"
            @click="colony.build(item.definition.facility_id)"
          >
            <Pickaxe class="mr-1 inline h-3 w-3" />
            {{ item.maxed ? '已满' : costText(item.cost) || '待定稿' }}
          </button>
        </div>
        <p class="mt-1 text-[10px] text-terminal-dim">{{ item.effectText }}</p>
      </div>
    </div>
  </section>
</template>
