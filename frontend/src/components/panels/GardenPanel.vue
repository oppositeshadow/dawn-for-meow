<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { FlaskConical, Sprout, Scissors, Expand } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import GaugeBar from '@/components/common/GaugeBar.vue'
import { useGardenStore, type GardenTile } from '@/stores/garden'

const garden = useGardenStore()
const selectedSeed = ref('ordinary_moss')

onMounted(() => garden.startPolling(5000))
onUnmounted(() => garden.stopPolling())

const view = computed(() => garden.garden)
const grid = computed(() => {
  const tiles = [...(view.value?.grid ?? [])]
  return tiles.sort((a, b) => (a.y - b.y) || (a.x - b.x))
})

function tileClass(tile: GardenTile): string {
  if (!tile.unlocked) return 'border-terminal-line/40 text-terminal-dim/40'
  if (!tile.seed_id) return 'border-terminal-line text-terminal-dim hover:border-terminal-accent/60'
  if (tile.stage === 'WITHERED') return 'border-terminal-crit/60 text-terminal-crit'
  if (tile.stage === 'MATURE') return 'border-terminal-accent bg-terminal-accent/15 text-terminal-accent'
  if (tile.stage === 'JOINTING') return 'border-terminal-accent/40 text-terminal-text'
  return 'border-terminal-line text-terminal-text'
}

function tileLabel(tile: GardenTile): string {
  if (!tile.unlocked) return '🔒'
  if (!tile.seed_id) return '·'
  return { SEEDLING: '芽', JOINTING: '节', MATURE: '熟', WITHERED: '枯' }[tile.stage ?? ''] ?? '?'
}

function tileTitle(tile: GardenTile): string {
  if (!tile.unlocked) return `(${tile.x},${tile.y}) 未解锁`
  if (!tile.seed_id) return `(${tile.x},${tile.y}) 空地：点击播种`
  return `${tile.plant_name}｜${tile.stage}｜${tile.age.toFixed(0)}s｜突变进度 ${(tile.mutation_progress * 100).toFixed(0)}%（点击采摘）`
}

function clickTile(tile: GardenTile) {
  if (garden.busy || !tile.unlocked) return
  if (!tile.seed_id) {
    void garden.plant(tile.x, tile.y, selectedSeed.value)
  } else if (tile.stage === 'MATURE') {
    void garden.harvest(tile.x, tile.y)
  }
}

function haloText(): string {
  const halo = view.value?.halo ?? {}
  const parts: string[] = []
  if (halo.power_kw) parts.push(`+${halo.power_kw} kW`)
  if (halo.suspicion_per_sec) parts.push(`警戒 ${halo.suspicion_per_sec}/s`)
  if (halo.vehicle_armor) parts.push(`装甲 +${Math.round(halo.vehicle_armor * 100)}%`)
  return parts.join(' · ') || '暂无在田光环'
}

function costText(cost: Record<string, number>): string {
  return Object.entries(cost).map(([key, value]) => `${value} ${key === 'scrap' ? '废铁' : key === 'chips' ? '芯片' : key}`).join(' + ')
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <FlaskConical class="h-3.5 w-3.5" />
      <span>猫草实验室</span>
      <Badge :text="`图鉴 ${view?.codex.length ?? 0}/${view?.codex_total ?? 0}`" tone="accent" />
      <Badge :text="`${view?.unlocked_cells ?? 9}/49 格`" />
      <Badge v-if="view?.mechanical_arm_enabled" text="机械臂" tone="warn" />
    </div>

    <div class="flex-1 space-y-2 overflow-auto p-3 text-[12px]">
      <!-- 7×7 格盘 -->
      <div class="grid grid-cols-7 gap-1">
        <button
          v-for="tile in grid"
          :key="`${tile.x}-${tile.y}`"
          class="flex h-8 items-center justify-center rounded border text-[11px] transition-colors disabled:cursor-not-allowed"
          :class="tileClass(tile)"
          :title="tileTitle(tile)"
          :disabled="garden.busy || !tile.unlocked"
          @click="clickTile(tile)"
        >
          {{ tileLabel(tile) }}
        </button>
      </div>

      <!-- 操作区 -->
      <div class="flex flex-wrap items-center gap-2">
        <select v-model="selectedSeed" class="rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]">
          <option v-for="seed in garden.unlockedSeeds" :key="seed.id" :value="seed.id">
            {{ seed.name }}（{{ Object.entries(seed.harvest).map(([k, v]) => `${k === 'catnip' ? '薄荷' : k === 'byte_credits' ? '算力币' : k === 'research_points' ? '算力' : k} ${v}`).join(' ') }}）
          </option>
        </select>
        <button class="btn px-2 py-0.5" :disabled="garden.busy" @click="garden.expand()">
          <Expand class="mr-1 inline h-3 w-3" />扩建（{{ costText(view?.expansion_cost ?? {}) }}）
        </button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <span class="text-[10px] text-terminal-dim">培养液</span>
        <select
          :value="view?.current_medium"
          class="rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]"
          @change="garden.setMedium(($event.target as HTMLSelectElement).value)"
        >
          <option v-for="(name, key) in view?.media ?? {}" :key="key" :value="key">{{ name }}</option>
        </select>
        <label class="flex items-center gap-1 text-[11px]">
          <input
            type="checkbox"
            class="accent-terminal-accent"
            :checked="view?.mechanical_arm_enabled"
            @change="garden.setArm(($event.target as HTMLInputElement).checked)"
          />
          机械臂托管
        </label>
        <label class="flex items-center gap-1 text-[11px]">
          <input
            type="checkbox"
            class="accent-terminal-accent"
            :checked="view?.auto_protect_unknown"
            @change="garden.setArm(view?.mechanical_arm_enabled ?? false, ($event.target as HTMLInputElement).checked)"
          />
          保护未知突变
        </label>
      </div>

      <!-- 在田光环 -->
      <div class="space-y-1 border-t border-terminal-line pt-2">
        <div class="flex items-center gap-2 text-[11px]">
          <Sprout class="h-3 w-3 text-terminal-accent" />
          <span class="text-terminal-dim">在田光环</span>
          <span>{{ haloText() }}</span>
        </div>
        <div class="text-[10px] text-terminal-dim">
          图鉴：{{ view?.codex.map((id) => view?.plants[id]?.name).join('、') || '—' }}
        </div>
      </div>

      <!-- 说明 -->
      <div class="space-y-1 border-t border-terminal-line pt-2 text-[10px] text-terminal-dim">
        <div class="flex items-center gap-1">
          <Scissors class="h-3 w-3" />点空地播种 · 点发亮的「熟」采摘（无背包，直接变现）
        </div>
        <div>放射液：生长 ×2、突变 ×3、成熟 120 秒后枯萎｜零重力：生长 ×0.5、永不枯萎（攒突变用）</div>
      </div>
    </div>
  </section>
</template>
