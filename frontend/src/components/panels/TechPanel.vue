<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { FlaskConical, Lock, CheckCircle2, Dices } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import GaugeBar from '@/components/common/GaugeBar.vue'
import { useTechStore, type TechNodeView } from '@/stores/tech'
import { useColonyStore } from '@/stores/colony'
import { formatDuration } from '@/utils/format'

const tech = useTechStore()
const colony = useColonyStore()

const TIER_NAMES: Record<number, string> = {
  1: 'Tier 1 · 避难所初建',
  2: 'Tier 2 · 工业萌芽与隐匿',
  3: 'Tier 3 · 战车武装与深网',
  4: 'Tier 4 · 航天破晓',
}

onMounted(() => tech.startPolling(5000))
onUnmounted(() => tech.stopPolling())

// 把"派猫"与"省时间"连起来：在研节点上再派 1 只极客猫能省多少
const researchSaving = computed(() => {
  const rate = tech.tree?.research_points_per_sec ?? 0
  const node = tech.tree?.researching
  if (!node || rate <= 0) return null
  const remaining = Math.max(0, node.display_cost - node.current_progress)
  if (remaining <= 0) return null
  const saved = remaining / rate - remaining / (rate + 1)
  if (saved < 60) return null
  return `· 再派 1 只极客猫可省约 ${formatDuration(saved)}`
})

// "科研起步窄门"：图灵终端建了但净电力为负 ⇒ 断电、科研永远不动，玩家看不出原因
const blackoutHint = computed(() => {
  const hasTerminal = (colony.facilities.turing_terminal ?? 0) > 0
  if (!hasTerminal) return null
  const net = colony.power?.net_kw ?? 0
  if (net >= 0) return null
  return `图灵终端断电了（净电力 ${net.toFixed(0)} kW）：造两座【猫力发电滚轮】并派猫踩轮，科研才会转起来`
})

// 效果人话化（《数值平衡表》§6.4）：百分比类键加成数、其余直接报数值
const PERCENT_KEYS = new Set([
  'catnip_efficiency',
  'fleet_armor',
  'armor_bonus',
  'dps_bonus',
  'smelt_speed',
  'smelt_yield',
  'vs_shield',
])

function effectText(effects: Record<string, number | string | boolean>): string {
  return Object.entries(effects)
    .map(([key, value]) => {
      if (typeof value === 'number') {
        return PERCENT_KEYS.has(key) ? `${key} +${Math.round(value * 100)}%` : `${key} ${value}`
      }
      return `${key} ${String(value)}`
    })
    .join('、')
}

// 预估耗时（用当前极客产出算）：把"还要 129600 秒"变成"还要 2.4 小时"
function etaText(node: TechNodeView): string | null {
  const rate = tech.tree?.research_points_per_sec ?? 0
  if (rate <= 0) return null
  const remaining = Math.max(0, node.display_cost - node.current_progress)
  if (remaining <= 0) return null
  return formatDuration(remaining / rate)
}

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
    <div v-if="blackoutHint" class="border-b border-terminal-warn/40 bg-terminal-warn/5 px-3 py-2 text-[11px] text-terminal-warn">
      {{ blackoutHint }}
    </div>
    <div class="panel-title">
      <FlaskConical class="h-3.5 w-3.5" />
      <span>科技树</span>
      <Badge
        :text="`科研等级 ${tech.tree?.research_tier_level ?? 1}`"
        tone="accent"
      />
      <Badge :text="`${tech.tree?.unlocked_count ?? 0}/${tech.tree?.total_nodes ?? 19}`" />
      <span class="ml-auto text-[10px]">算力 {{ (tech.tree?.research_points_per_sec ?? 0).toFixed(1) }}/s</span>
      <span v-if="researchSaving" class="text-[10px] text-terminal-warn">{{ researchSaving }}</span>
    </div>

    <div v-if="tech.tree?.researching" class="space-y-1 border-b border-terminal-line px-3 py-2">
      <div class="flex items-center gap-2 text-[12px]">
        <span class="text-terminal-warn">{{ tech.tree.researching.tech_name }}</span>
        <span class="text-[11px] text-terminal-dim">
          {{ tech.tree.researching.current_progress.toFixed(0) }}/{{ tech.tree.researching.display_cost.toFixed(0) }}
          <template v-if="tech.tree.researching.eta_seconds !== null">
            · 还需约 {{ formatDuration(tech.tree.researching.eta_seconds) }}
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
            <!-- 用当前极客产出估算"点下去要等多久"，避免玩家在 18 小时的节点上盲选 -->
            <p v-if="node.available && etaText(node)" class="mt-0.5 text-[10px] text-terminal-dim">
              当前极客产出下约需 {{ etaText(node) }}
            </p>
          <p v-if="Object.keys(node.active_effects).length" class="mt-0.5 text-[10px] text-terminal-accent">
            生效中：{{ effectText(node.active_effects) }}
          </p>
          <p v-if="Object.keys(node.pending_effects).length" class="mt-0.5 text-[10px] text-terminal-dim">
            卡面机制（待接线）：{{ effectText(node.pending_effects) }}
          </p>
        </div>
      </div>
    </div>
  </section>
</template>
