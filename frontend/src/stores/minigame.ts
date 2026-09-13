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

  function startPolling(intervalMs = 15000) {
    if (timer !== null) return
    void refresh()
    timer = window.setInterval(refresh, intervalMs)
  }

  function stopPolling() {
    if (timer !== null) window.clearInterval(timer)
    timer = null
  }

  return { view, busy, refresh, submitGuess, startPolling, stopPolling }
})
