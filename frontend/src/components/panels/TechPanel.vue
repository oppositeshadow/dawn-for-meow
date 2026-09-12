<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { FlaskConical, Lock, CheckCircle2, Dices } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import GaugeBar from '@/components/common/GaugeBar.vue'
import { useTechStore, type TechNodeView } from '@/stores/tech'

const tech = useTechStore()

const TIER_NAMES: Record<number, string> = {
  1: 'Tier 1 · 避难所初建',
  2: 'Tier 2 · 工业萌芽与隐匿',
  3: 'Tier 3 · 战车武装与深网',
  4: 'Tier 4 · 航天破晓',
}

onMounted(() => tech.startPolling(5000))
onUnmounted(() => tech.stopPolling())

const tiers = computed(() =>
  Object.keys(tech.nodesByTier)
    .map(Number)
    .sort((a, b) => a - b)
    .map((tier) => ({ tier, nodes: tech.nodesByTier[tier] ?? [] })),
)

function nodeTone(node: TechNodeView): 'accent' | 'warn' | 'dim' {
  if (node.status === 'UNLOCKED') return 'accent'
  if (node.status === 'RESEARCHING') return 'warn'
  return 'dim'
}

function statusText(node: TechNodeView): string {
  if (node.status === 'UNLOCKED') return '已解锁'
  if (node.status === 'RESEARCHING') return '研发中'
  if (node.missing_parents.length) return '前置未满足'
  // available=false 且不是前置问题 ⇒ 单线程研发被别的节点占着
  if (!node.available) return '排队中'
  return '可研发'
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <FlaskConical class="h-3.5 w-3.5" />
      <span>科技树</span>
      <Badge
        :text="`科研等级 ${tech.tree?.research_tier_level ?? 1}`"
        tone="accent"
      />
      <Badge :text="`${tech.tree?.unlocked_count ?? 0}/${tech.tree?.total_nodes ?? 19}`" />
      <span class="ml-auto text-[10px]">算力 {{ (tech.tree?.research_points_per_sec ?? 0).toFixed(1) }}/s</span>
    </div>

    <div v-if="tech.tree?.researching" class="space-y-1 border-b border-terminal-line px-3 py-2">
      <div class="flex items-center gap-2 text-[12px]">
        <span class="text-terminal-warn">{{ tech.tree.researching.tech_name }}</span>
        <span class="text-[11px] text-terminal-dim">
          {{ tech.tree.researching.current_progress.toFixed(0) }}/{{ tech.tree.researching.display_cost.toFixed(0) }}
          <template v-if="tech.tree.researching.eta_seconds !== null">
            · 约 {{ Math.ceil(tech.tree.researching.eta_seconds) }}s
          </template>
          <template v-else>· 派极客猫上工才能推进</template>
        </span>
      </div>
      <GaugeBar :value="tech.tree.researching.percent" :max="100" tone="warn" :height="4" />
    </div>

    <div class="flex-1 space-y-3 overflow-auto p-3">
      <div v-if="!tech.tree" class="text-[12px] text-terminal-dim">正在读取科技树……</div>
      <div v-for="group in tiers" :key="group.tier" class="space-y-1">
        <div class="text-[11px] text-terminal-dim">{{ TIER_NAMES[group.tier] ?? `Tier ${group.tier}` }}</div>
        <div
          v-for="node in group.nodes"
          :key="node.tech_id"
          class="rounded border border-terminal-line/70 px-2 py-1.5"
          :class="node.status === 'UNLOCKED' ? 'border-terminal-accent/40' : ''"
        >
          <div class="flex items-center gap-2">
            <CheckCircle2 v-if="node.status === 'UNLOCKED'" class="h-3.5 w-3.5 text-terminal-accent" />
            <Lock v-else-if="node.status === 'LOCKED'" class="h-3.5 w-3.5 text-terminal-dim" />
            <FlaskConical v-else class="h-3.5 w-3.5 text-terminal-warn" />
            <span class="truncate text-[12px]">{{ node.tech_name }}</span>
            <Badge :text="statusText(node)" :tone="nodeTone(node)" />
            <button
              class="btn ml-auto px-1.5 py-0.5"
              :disabled="!node.available || tech.busyTechId !== null"
              @click="tech.research(node.tech_id)"
            >
              {{ node.display_cost.toFixed(0) }} 算力
            </button>
            <button
              v-if="node.is_agent_generated"
              class="btn px-1.5 py-0.5"
              :disabled="tech.busyTechId !== null"
              @click="tech.reroll(node.tech_id)"
            >
              <Dices class="h-3 w-3" />
            </button>
          </div>
          <p v-if="node.flavor_text" class="mt-1 text-[10px] text-terminal-dim">{{ node.flavor_text }}</p>
        </div>
      </div>
    </div>
  </section>
</template>
