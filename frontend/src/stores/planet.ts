import { defineStore } from 'pinia'
import { ref } from 'vue'

import { useColonyStore } from '@/stores/colony'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export interface LogisticsRoute {
  route_id: string
  from_planet: number
  to_planet: number
  cat_count: number
  departed_at: number
  arrives_at: number
  raided: boolean
}

export interface PlanetView {
  planet_id: number
  name: string
  unlocked: boolean
  is_active: boolean
  unlocked_at: number | null
  biome_tag: string | null
  logistics_routes: LogisticsRoute[]
  traits: {
    capacity_multiplier: number
    catnip_multiplier: number
    output_bonus: Record<string, number>
  }
  /** 星球周期（《数值平衡表》§15.5）：当期事件、相位与剩余秒数 */
  cycle: {
    name: string
    label: string
    phase: 'HIGH' | 'LOW' | 'NEUTRAL'
    /** 整轮秒数与当前相位长度（后端 `balance.PLANET_CYCLES` 口径，前端只用来画进度条） */
    period: number
    phase_seconds: number
    seconds_left: number
    solar_multiplier: number
    production_multiplier: number
    raid_multiplier: number
  }
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

/** 星区星图与跨星迁猫仓（星际第①②步）。 */
export const usePlanetStore = defineStore('planet', () => {
  const colony = useColonyStore()
  const planets = ref<PlanetView[]>([])
  const busy = ref(false)
  let timer: number | null = null

  async function refresh() {
    try {
      const payload = await request<{ data: { planets: PlanetView[] } }>(
        `/planet/state?slot=${colony.slotId}`,
      )
      planets.value = payload.data.planets
    } catch {
      /* 后端不可用时保留上一次视图 */
    }
  }

  async function migrate(toPlanet: number, count: number, cargo: Record<string, number> = {}) {
    busy.value = true
    try {
      const payload = await request<{ data: { eta_seconds: number; slots_used: number; slots_total: number } }>(
        '/planet/migrate',
        {
          method: 'POST',
          body: JSON.stringify({
            slot: colony.slotId,
            from_planet: 0,
            to_planet: toPlanet,
            count,
            ...(Object.keys(cargo).length ? { cargo } : {}),
          }),
        },
      )
      const cargoText = Object.entries(cargo)
        .filter(([, amount]) => amount > 0)
        .map(([resource, amount]) => `${amount} ${resource}`)
        .join('、')
      colony.log(
        `航线出发：${count} 只猫前往${planets.value.find((p) => p.planet_id === toPlanet)?.name ?? `星球 ${toPlanet}`}，` +
          `预计 ${payload.data.eta_seconds} 秒抵达（在途 ${payload.data.slots_used}/${payload.data.slots_total} 条）` +
          (cargoText ? `｜随船：${cargoText}` : ''),
      )
      await refresh()
      await colony.refresh({ silent: true })
    } catch (error) {
      colony.log(`迁猫失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
    } finally {
      busy.value = false
    }
  }

  async function switchTo(planetId: number) {
    try {
      await request('/planet/switch', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, planet_id: planetId }),
      })
      await colony.refresh({ silent: true })
      await refresh()
      colony.log(`已切换到${planets.value.find((p) => p.planet_id === planetId)?.name ?? `星球 ${planetId}`}`)
    } catch (error) {
      colony.log(`切星失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
    }
  }

  function startPolling(intervalMs = 10000) {
    if (timer !== null) return
    void refresh()
    timer = window.setInterval(refresh, intervalMs)
  }

  function stopPolling() {
    if (timer !== null) window.clearInterval(timer)
    timer = null
  }

  return { planets, busy, refresh, migrate, switchTo, startPolling, stopPolling }
})
