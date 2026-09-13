<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Orbit, Rocket } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import { useColonyStore } from '@/stores/colony'
import { usePlanetStore } from '@/stores/planet'

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
      </div>
    </div>
  </section>
</template>
