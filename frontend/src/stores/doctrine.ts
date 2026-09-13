import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { useColonyStore } from '@/stores/colony'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export interface DoctrineItem {
  doctrine_id: string
  name: string
  cost: number
  effect_text: string
  effects: Record<string, number>
  unlocked: boolean
  affordable: boolean
}

export interface DoctrineView {
  unity: number
  unlocked_count: number
  total: number
  wired_effects: string[]
  doctrines: DoctrineItem[]
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

/** 星系法典政令仓（《数值平衡表》§15.2）。 */
export const useDoctrineStore = defineStore('doctrine', () => {
  const colony = useColonyStore()
  const view = ref<DoctrineView | null>(null)
  const busy = ref(false)

  const unlocked = computed(() => (view.value?.doctrines ?? []).filter((item) => item.unlocked))

  async function refresh() {
    try {
      const payload = await request<{ data: DoctrineView }>(`/doctrine/list?slot=${colony.slotId}`)
      view.value = payload.data
    } catch {
      /* 后端不可用时保留上一次视图 */
    }
  }

  async function unlock(doctrineId: string) {
    busy.value = true
    try {
      const payload = await request<{ data: { name: string; cost: number; unity_left: number; pending: Record<string, number> } }>(
        '/doctrine/unlock',
        { method: 'POST', body: JSON.stringify({ slot: colony.slotId, doctrine_id: doctrineId }) },
      )
      const pending = Object.keys(payload.data.pending ?? {})
      colony.log(
        `法典点亮【${payload.data.name}】：消耗 ${payload.data.cost} 凝聚力，余 ${payload.data.unity_left}` +
          (pending.length ? `（其中 ${pending.join('、')} 尚未接线，仅展示）` : ''),
      )
      await refresh()
    } catch (error) {
      colony.log(`点亮失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
    } finally {
      busy.value = false
    }
  }

  return { view, busy, unlocked, refresh, unlock }
})
