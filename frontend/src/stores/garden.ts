import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { useColonyStore } from '@/stores/colony'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export interface GardenTile {
  x: number
  y: number
  unlocked: boolean
  seed_id: string | null
  plant_name: string | null
  school: string | null
  stage: string | null
  age: number
  mutation_progress: number
}

export interface GardenStateView {
  grid_size: number
  unlocked_cells: number
  current_medium: string
  mechanical_arm_enabled: boolean
  auto_protect_unknown: boolean
  last_tick_time: number
  grid: GardenTile[]
  /** 真的在结算的在田光环（§10 接线状态） */
  halo: Record<string, number>
  /** 只展示、尚未接线的光环（界面必须标「待接线」，不能混进生效数字） */
  halo_pending: Record<string, number>
  codex: string[]
  codex_total: number
  codex_papers: Record<
    string,
    { plant_id: string; name: string | null; school: string | null; title: string; body: string; source: string; at: number }
  >
  plants: Record<
    string,
    {
      name: string
      school: string
      harvest: Record<string, number>
      halo: Record<string, number>
      halo_pending: Record<string, number>
      growth_multiplier: number
      unlocked: boolean
    }
  >
  schools: Record<string, { name: string; tag: string }>
  expansion_cost: Record<string, number>
  media: Record<string, string>
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new Error(`${payload?.message ?? response.status}${payload?.detail ? `：${payload.detail}` : ''}`)
  }
  return payload as T
}

/** 猫草水培实验室仓（模块 H）。 */
export const useGardenStore = defineStore('garden', () => {
  const colony = useColonyStore()
  const garden = ref<GardenStateView | null>(null)
  const busy = ref(false)
  let timer: number | null = null

  const unlockedSeeds = computed(() => {
    const view = garden.value
    if (!view) return []
    return view.codex.map((id) => ({ id, ...view.plants[id] }))
  })

  async function refresh() {
    try {
      const payload = await request<{ data: GardenStateView }>(
        `/garden/state?slot=${colony.slotId}&planet_id=${colony.planetId}`,
      )
      garden.value = payload.data
    } catch {
      /* 后端不可用时保留上一次视图 */
    }
  }

  async function act(body: Record<string, unknown>, label: string, after?: (data: Record<string, unknown>) => void) {
    busy.value = true
    try {
      const payload = await request<{ data: Record<string, unknown> }>('/garden/action', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, planet_id: colony.planetId, ...body }),
      })
      if (after) after(payload.data)
      await refresh()
      await colony.refresh({ silent: true })
    } catch (error) {
      colony.log(`${label}失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
    } finally {
      busy.value = false
    }
  }

  function plant(x: number, y: number, seedId: string) {
    return act({ action: 'PLANT', x, y, seed_id: seedId }, '播种', (data) =>
      colony.log(`播种：(${x},${y}) 种下了【${data.plant_name}】`),
    )
  }

  function harvest(x: number, y: number) {
    return act({ action: 'HARVEST', x, y }, '采摘', (data) => {
      const gained = Object.entries((data.gained as Record<string, number>) ?? {})
        .map(([key, value]) => `${key} +${value}`)
        .join('、')
      colony.log(`采摘【${data.plant_name}】：${gained || '无产出'}`)
      if (data.new_codex_entry) {
        colony.log(`图鉴解锁新母本（共 ${data.codex_size} 种）`, 'warn')
        const paper = data.codex_paper as { title: string; source: string } | null | undefined
        if (paper) colony.log(`《异星植物学图鉴》新增条目《${paper.title}》（来源 ${paper.source}）`)
      }
    })
  }

  function setMedium(medium: string) {
    return act({ action: 'MEDIUM', medium }, '切换培养液', (data) =>
      colony.log(`培养液切换为【${data.name}】（生长 ×${data.growth_multiplier}）`),
    )
  }

  function expand() {
    return act({ action: 'EXPAND' }, '扩建', (data) =>
      colony.log(`扩建完成：解锁 (${(data.new_cell as { x: number }).x},${(data.new_cell as { y: number }).y})，共 ${data.unlocked_cells} 格`),
    )
  }

  function setArm(enabled: boolean, autoProtectUnknown?: boolean) {
    return act(
      { action: 'ARM', enabled, ...(autoProtectUnknown === undefined ? {} : { auto_protect_unknown: autoProtectUnknown }) },
      '机械臂设置',
      (data) => colony.log(`机械臂：${data.mechanical_arm_enabled ? '开启' : '关闭'}（保护未知突变：${data.auto_protect_unknown ? '是' : '否'}）`),
    )
  }

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
    garden,
    busy,
    unlockedSeeds,
    refresh,
    plant,
    harvest,
    setMedium,
    expand,
    setArm,
    startPolling,
    stopPolling,
  }
})
