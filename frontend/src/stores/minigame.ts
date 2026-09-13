import { defineStore } from 'pinia'
import { ref } from 'vue'

import { useColonyStore } from '@/stores/colony'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export interface CipherGuess {
  guess: number[]
  exact: number
  partial: number
}

export interface MinigameEntry {
  minigame_id: string
  name: string
  planet_id: number
  planet_name: string
  planet_unlocked: boolean
  best_score: number
  play_count: number
  quota: Record<string, unknown>
  core_loop: string | null
  reward: Record<string, unknown> | null
  state?: {
    day: string
    used: number
    remaining: number
    guesses: CipherGuess[]
    code_length: number
    symbol_count: number
    best_attempts: number
    // 矿脉扫描
    quota?: number
    quota_max?: number
    regen_seconds?: number
    next_regen_in?: number
    board_size?: number
    vein_count?: number
    found_count?: number
    board_index?: number
    revealed?: Array<{ x: number; y: number; hint: number }>
    // 熔炉配比
    recipes_found?: number
    recipe_cap?: number
    smelt_speed_bonus?: number
    attempts?: number
    scrap_cost?: number
    last_feedback?: { result: string; hint: string } | null
  }
}

export interface MinigameView {
  games: MinigameEntry[]
  intel_level: number
  convoy_ends_at: number | null
  note: string
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

/** 小游戏仓（模块 O）：目前落地密电译码。 */
export const useMinigameStore = defineStore('minigame', () => {
  const colony = useColonyStore()
  const view = ref<MinigameView | null>(null)
  const busy = ref(false)
  let timer: number | null = null

  async function refresh() {
    try {
      const payload = await request<{ data: MinigameView }>(`/minigame/list?slot=${colony.slotId}`)
      view.value = payload.data
    } catch {
      /* 后端不可用时保留旧视图 */
    }
  }

  async function submitGuess(guess: number[]) {
    busy.value = true
    try {
      const payload = await request<{ data: Record<string, any> }>('/minigame/action', {
        method: 'POST',
        body: JSON.stringify({
          slot: colony.slotId,
          minigame_id: 'cipher_decode',
          action: 'SUBMIT_GUESS',
          payload: { guess },
        }),
      })
      const data = payload.data
      colony.log(
        data.solved
          ? `密电译码成功！${data.best_attempts} 步破解（当天密钥 ${data.answer}），情报破译度 → ${(data.reward?.intel_level * 100).toFixed(0)}%`
          : `密电译码：位置对 ${data.exact} 个、符号对 ${data.partial} 个（剩余 ${data.remaining} 次）`,
        data.solved ? 'info' : 'warn',
      )
      await refresh()
      return data
    } catch (error) {
      colony.log(`密电译码失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
      return null
    } finally {
      busy.value = false
    }
  }

  async function send(data: Record<string, unknown>, log: (payload: any) => string | null, fallback: string) {
    busy.value = true
    try {
      const payload = await request<{ data: Record<string, any> }>('/minigame/action', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, ...data }),
      })
      const line = log(payload.data) ?? fallback
      colony.log(line, payload.data.solved === false ? 'warn' : 'info')
      await refresh()
      return payload.data
    } catch (error) {
      colony.log(`${fallback}失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
      return null
    } finally {
      busy.value = false
    }
  }

  const scanVein = (x: number, y: number) =>
    send(
      { minigame_id: 'vein_scan', action: 'SCAN', payload: { x, y } },
      (data) =>
        data.is_vein
          ? `矿脉扫描 (${x},${y})：命中！合金 +${data.gained.alloys}、电池 +${data.gained.battery}（剩余配额 ${data.quota}）`
          : `矿脉扫描 (${x},${y})：周边矿脉 ${data.hint} 处（剩余配额 ${data.quota}）`,
      '矿脉扫描',
    )

  const submitMix = (mix: number[]) =>
    send(
      { minigame_id: 'forge_recipe', action: 'SUBMIT_MIX', payload: { mix } },
      (data) =>
        data.reward
          ? `熔炉配比命中：${data.reward.message}`
          : `熔炉配比：${data.feedback.hint}（已消耗 ${data.scrap_cost ?? 20} 废铁）`,
      '熔炉配比',
    )

  function startPolling(intervalMs = 15000) {
    if (timer !== null) return
    void refresh()
    timer = window.setInterval(refresh, intervalMs)
  }

  function stopPolling() {
    if (timer !== null) window.clearInterval(timer)
    timer = null
  }

  return { view, busy, refresh, submitGuess, scanVein, submitMix, startPolling, stopPolling }
})
