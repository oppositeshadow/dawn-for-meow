<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Orbit, Rocket } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import GaugeBar from '@/components/common/GaugeBar.vue'
import { useColonyStore } from '@/stores/colony'
import { usePlanetStore, type PlanetView } from '@/stores/planet'

const colony = useColonyStore()
const planet = usePlanetStore()

const target = ref<number>(1)
const count = ref<number>(2)
const cargoScrap = ref<number>(30)
const now = ref(Math.floor(Date.now() / 1000))
let clock: number | null = null

onMounted(() => {
  planet.startPolling(10000)
  clock = window.setInterval(() => (now.value = Math.floor(Date.now() / 1000)), 1000)
})
onUnmounted(() => {
  planet.stopPolling()
  if (clock !== null) window.clearInterval(clock)
})

const unlocked = computed(() => planet.planets.filter((item) => item.unlocked && item.planet_id !== 0))

function eta(route: { arrives_at: number }): string {
  const left = route.arrives_at - now.value
  return left <= 0 ? '即将抵达' : `${left} 秒`
}

/** 星球周期文案：`岩浆潮汐 · 高潮（还剩 4 分钟）`（§15.5） */
function cycleText(cycle: PlanetView['cycle']): string | null {
  if (!cycle || !cycle.name) return null
  const left = Math.max(0, cycle.seconds_left)
  const time = left >= 60 ? `${Math.ceil(left / 60)} 分钟` : `${left} 秒`
  return `${cycle.name} · ${cycle.label}（还剩 ${time}）`
}

/** 当期正/负效果提示：让玩家知道"现在适合干什么" */
function cycleEffectText(cycle: PlanetView['cycle']): string | null {
  if (!cycle || !cycle.name) return null
  const parts: string[] = []
  if (cycle.solar_multiplier !== 1) parts.push(`发电 ×${cycle.solar_multiplier}`)
  if (cycle.production_multiplier !== 1) parts.push(`产出 ×${cycle.production_multiplier}`)
  if (cycle.raid_multiplier !== 1) parts.push(`被劫掠 ×${cycle.raid_multiplier}`)
  return parts.join('｜') || null
}

/** 迁猫前的风险提示（§15.5）：目标星球正处"被劫掠 ×N"的相位时明说一句，别让玩家静默踩雷 */
const targetRaidHint = computed(() => {
  const star = planet.planets.find((item) => item.planet_id === target.value)
  const cycle = star?.cycle
  if (!cycle || !cycle.name || cycle.raid_multiplier <= 1) return null
  return `⚠ ${star?.name ?? '目标星球'}正处于【${cycle.name}·${cycle.label}】：这趟被劫掠概率 ×${cycle.raid_multiplier}，介意就等相位过去再发`
})

// 星球性格（后端 planet_traits 透出）：只展示，不在这里算系数
function traitText(traits: { capacity_multiplier: number; catnip_multiplier: number; output_bonus: Record<string, number> }): string {
  const parts = [
    `承载力 ×${traits.capacity_multiplier}`,
    `产粮 ×${traits.catnip_multiplier}`,
  ]
  for (const [resource, factor] of Object.entries(traits.output_bonus ?? {})) {
    if (factor !== 1) parts.push(`${resource === 'scrap' ? '废铁' : resource === 'chips' ? '芯片' : resource} ×${factor}`)
  }
  return parts.join(' ｜ ')
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <Orbit class="h-3.5 w-3.5" />
      <span>星区星图</span>
      <Badge :text="`已解锁 ${unlocked.length} / 3`" tone="accent" />
    </div>

    <div class="flex-1 space-y-3 overflow-auto p-3 text-[12px]">
      <div
        v-for="item in planet.planets"
        :key="item.planet_id"
        class="space-y-1 rounded border p-2"
        :class="item.is_active ? 'border-terminal-accent/50' : 'border-terminal-line/70'"
      >
        <div class="flex items-center gap-2">
          <span :class="item.is_active ? 'text-terminal-accent' : 'text-terminal-text'">{{ item.name }}</span>
          <Badge v-if="item.is_active" text="当前" tone="accent" />
          <Badge v-if="!item.unlocked" text="未解锁" tone="warn" />
          <button
            v-if="item.unlocked && !item.is_active"
            class="btn ml-auto px-2 py-0.5"
            :disabled="planet.busy"
            @click="planet.switchTo(item.planet_id)"
          >
            切到此星
          </button>
        </div>
        <p v-if="item.biome_tag" class="text-[10px] text-terminal-dim">{{ item.biome_tag }}</p>
        <p v-if="item.planet_id !== 0" class="text-[10px] text-terminal-warn/80">{{ traitText(item.traits) }}</p>
        <p v-if="cycleText(item.cycle)" class="text-[10px] text-terminal-accent/80">
          {{ cycleText(item.cycle) }}
          <span v-if="cycleEffectText(item.cycle)" class="text-terminal-dim">｜{{ cycleEffectText(item.cycle) }}</span>
        </p>
        <!-- 相位进度条：只画"这段潮走了多少"，不替玩家判断好坏（同一相位在三颗星利弊不同） -->
        <GaugeBar
          v-if="item.cycle.phase_seconds > 0"
          :value="item.cycle.phase_seconds - item.cycle.seconds_left"
          :max="item.cycle.phase_seconds"
          :height="3"
        />
        <div v-if="item.logistics_routes.length" class="space-y-0.5 text-[10px] text-terminal-dim">
          <div v-for="route in item.logistics_routes" :key="route.route_id">
            在途：{{ route.cat_count }} 只猫 · {{ eta(route) }}
            <span v-if="route.raided" class="text-terminal-crit">（遭劫掠，延误中，猫一只不少）</span>
          </div>
        </div>
      </div>

      <div class="space-y-2 rounded border border-terminal-line/70 p-2">
        <div class="flex items-center gap-2">
          <Rocket class="h-3.5 w-3.5 text-terminal-accent" />
          <span>跨星迁猫</span>
          <span class="ml-auto text-[10px] text-terminal-dim">母星空闲 {{ colony.population.unassigned }} 只</span>
        </div>
        <div class="flex items-center gap-2 text-[11px]">
          <select v-model.number="target" class="flex-1 rounded border border-terminal-line/70 bg-transparent px-1">
            <option v-for="item in unlocked" :key="item.planet_id" :value="item.planet_id">{{ item.name }}</option>
          </select>
          <input
            v-model.number="count"
            type="number"
            min="1"
            max="20"
            class="w-16 rounded border border-terminal-line/70 bg-transparent px-1"
          >
          <button
            class="btn btn-primary px-2 py-0.5"
            :disabled="planet.busy"
            @click="planet.migrate(target, count, cargoScrap > 0 ? { scrap: cargoScrap } : {})"
          >
            派出
          </button>
        </div>
        <div class="flex items-center gap-2 text-[11px]">
          <span class="text-terminal-dim">随船废铁</span>
          <input
            v-model.number="cargoScrap"
            type="number"
            min="0"
            max="60"
            class="w-16 rounded border border-terminal-line/70 bg-transparent px-1"
          >
          <span class="text-terminal-dim">母星现有 {{ Math.floor(colony.resources.scrap) }}（单趟上限 60）</span>
        </div>
        <p class="text-[10px] text-terminal-dim">
          出发即离港、单趟 60 秒；在途期间两边都不占工位。被劫掠只延误 10 分钟，猫一只都不会丢。
          外星球从 0 起步、没有废墟可点，第一批必须随船带废铁，否则造不出纸箱窝。
        </p>
        <!-- 周期风险提示（§15.5）：只在目标星球正处"被劫掠 ×N"相位时出现，避免静默踩雷 -->
        <p v-if="targetRaidHint" class="text-[10px] text-terminal-crit">
          {{ targetRaidHint }}
        </p>
      </div>
    </div>
  </section>
</template>
