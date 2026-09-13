import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { useColonyStore } from '@/stores/colony'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export interface CareerView {
  playtime_seconds: number
  playtime_hours: number
  total_catnip: number
  total_scrap: number
  total_chips: number
  total_alloys: number
  total_battery: number
  total_kwh: number
  total_cats_born: number
  best_short_profit: number
  smuggling_volume: number
  expeditions_completed: number
  bombardment_survived: number
  fastest_rebuild_seconds: number | null
  achievements_unlocked: number
  achievements_total: number
  completed: boolean
}

export interface AchievementItem {
  achievement_id: string
  name: string
  desc: string
  target: number
  progress: number
  percent: number
  unlocked: boolean
  unlocked_at: number | null
  newly_unlocked: boolean
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

/** 生涯统计与成就馆仓（模块 N3）：只读聚合，不做任何写操作。 */
export const useStatsStore = defineStore('stats', () => {
  const colony = useColonyStore()
  const career = ref<CareerView | null>(null)
  const achievements = ref<AchievementItem[]>([])
  const busy = ref(false)
  let timer: number | null = null

  const unlockedCount = computed(() => achievements.value.filter((item) => item.unlocked).length)
  const sortedAchievements = computed(() =>
    [...achievements.value].sort((a, b) => Number(b.unlocked) - Number(a.unlocked) || b.percent - a.percent),
  )

  async function refresh() {
    busy.value = true
    try {
      const [careerPayload, badgePayload] = await Promise.all([
        request<{ data: CareerView }>(`/stats/career?slot=${colony.slotId}`),
        request<{ data: { achievements: AchievementItem[] } }>(`/stats/achievements?slot=${colony.slotId}`),
      ])
      career.value = careerPayload.data
      const items = badgePayload.data.achievements ?? []
      for (const item of items) {
        if (item.newly_unlocked) {
          colony.log(`成就点亮【${item.name}】：${item.desc}`, 'warn')
        }
      }
      achievements.value = items
    } catch {
      /* 后端不可用时保留上一次视图 */
    } finally {
      busy.value = false
    }
  }

  function startPolling(intervalMs = 20000) {
    if (timer !== null) return
    void refresh()
    timer = window.setInterval(refresh, intervalMs)
  }

  function stopPolling() {
    if (timer !== null) window.clearInterval(timer)
    timer = null
  }

  return {
    career,
    achievements,
    busy,
    unlockedCount,
    sortedAchievements,
    refresh,
    startPolling,
    stopPolling,
  }
})
