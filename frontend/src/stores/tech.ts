import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { useColonyStore } from '@/stores/colony'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export interface TechNodeView {
  tech_id: string
  tech_name: string
  tier: number
  node_order: number
  parent_ids: string[]
  missing_parents: string[]
  status: 'LOCKED' | 'RESEARCHING' | 'UNLOCKED'
  current_progress: number
  target_cost: number
  display_cost: number
  discount: number
  flavor_text: string | null
  mechanic_type: string | null
  is_agent_generated: boolean
  available: boolean
}

export interface TechResearchingView {
  tech_id: string
  tech_name: string
  tier: number
  current_progress: number
  display_cost: number
  percent: number
  eta_seconds: number | null
}

export interface TechTreeView {
  slot_id: number
  planet_id: number
  research_tier_level: number
  unlocked_count: number
  total_nodes: number
  tier_sizes: Record<string, number>
  total_cost: number
  research_points_per_sec: number
  researching: TechResearchingView | null
  nodes: TechNodeView[]
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new Error(`${payload?.message ?? response.status}${payload?.detail ? `：${payload.detail}` : ''}`)
  }
  return payload as T
}

/** 科技树仓（模块 E）。数据源：GET /tech/tree、POST /tech/research、POST /tech/reroll。 */
export const useTechStore = defineStore('tech', () => {
  const colony = useColonyStore()
  const tree = ref<TechTreeView | null>(null)
  const loading = ref(false)
  const busyTechId = ref<string | null>(null)
  let timer: number | null = null

  const nodesByTier = computed(() => {
    const groups: Record<number, TechNodeView[]> = {}
    for (const node of tree.value?.nodes ?? []) {
      groups[node.tier] = groups[node.tier] ?? []
      groups[node.tier].push(node)
    }
    return groups
  })

  async function refresh() {
    try {
      const response = await fetch(`${API_BASE}/tech/tree?slot=${colony.slotId}&planet_id=${colony.planetId}`)
      if (!response.ok) return
      const payload = (await response.json()) as { data: TechTreeView }
      tree.value = payload.data
    } catch {
      /* 后端不可用时保留上一次视图 */
    }
  }

  async function research(techId: string) {
    busyTechId.value = techId
    try {
      const node = tree.value?.nodes.find((item) => item.tech_id === techId)
      await post('/tech/research', { slot: colony.slotId, planet_id: colony.planetId, tech_id: techId })
      colony.log(`开始研发：${node?.tech_name ?? techId}（需 ${node?.display_cost ?? '?'} 算力）`)
      await refresh()
    } catch (error) {
      colony.log(`研发失败：${String(error instanceof Error ? error.message : error)}`, 'crit')
    } finally {
      busyTechId.value = null
    }
  }

  async function reroll(techId: string) {
    busyTechId.value = techId
    try {
      const payload = await post<{ data: { cost: number; card?: { tech_name: string; source: string } } }>(
        '/tech/reroll',
        { slot: colony.slotId, planet_id: colony.planetId, tech_id: techId },
      )
      const card = payload?.data?.card
      colony.log(
        card
          ? `重 Roll 完成：新卡面【${card.tech_name}】（来源 ${card.source}），消耗 ${payload.data.cost} 算力进度`
          : '重 Roll 完成：候选科技卡已刷新',
      )
      await refresh()
    } catch (error) {
      colony.log(`重 Roll 失败：${String(error instanceof Error ? error.message : error)}`, 'crit')
    } finally {
      busyTechId.value = null
    }
  }

  /** 面板打开时轮询（算力由后端离线结算推进，这里只做展示刷新） */
  function startPolling(intervalMs = 5000) {
    if (timer !== null) return
    void refresh()
    timer = window.setInterval(refresh, intervalMs)
  }

  function stopPolling() {
    if (timer !== null) window.clearInterval(timer)
    timer = null
  }

  return {
    tree,
    loading,
    busyTechId,
    nodesByTier,
    refresh,
    research,
    reroll,
    startPolling,
    stopPolling,
  }
})
