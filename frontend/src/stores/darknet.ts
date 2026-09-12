import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { useColonyStore } from '@/stores/colony'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export interface StockQuote {
  stock_id: string
  name: string
  price: number
  initial_price: number
  volatility: number
  spread: number
  trend_strength: number
  forecast: number
  target_forecast: number
  momentum: number
  ask: number
  bid: number
}

export interface DarknetView {
  byte_credits: number
  exposure: number
  exposure_band: string
  burner_id: string
  has_4s_data: boolean
  last_post_time: number | null
  post_cooldown_left: number
  stocks: StockQuote[]
  positions: Array<{ stock_id: string; shares: number; entry_price: number }>
  short_contracts: Array<{
    stock_id: string
    shares: number
    entry_price: number
    leverage: number
    margin: number
    liquidation_price: number
    ends_at: number
  }>
  pending_deliveries: Array<{ item_id: string; name: string; qty: number; ends_at: number }>
  whale_events: Array<{ ts: number; stock_id: string; side: string; amount: number }>
  market_items: Record<string, { name: string; base_price: number; resource: string; unit: number; current_price: number }>
  contraband: Record<string, { name: string; price: number; effect: string }>
  forum: Array<{
    id: number
    author_id: string
    is_player: boolean
    content: string
    telemetry_code: string | null
    author_post_count: number
    author_hit_rate: number
    target_stock: string | null
    sentiment: string | null
    persuasiveness: number | null
    reaction_pattern: string | null
  }>
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

/** 智械深网仓（模块 I）：行情 / 做多 / 做空 / 黑市 / 论坛做局 / 洗白。 */
export const useDarknetStore = defineStore('darknet', () => {
  const colony = useColonyStore()
  const darknet = ref<DarknetView | null>(null)
  const busy = ref(false)
  const now = ref(Math.floor(Date.now() / 1000))
  let timer: number | null = null
  let clock: number | null = null

  const holdings = computed(() => {
    const map: Record<string, number> = {}
    for (const item of darknet.value?.positions ?? []) {
      map[item.stock_id] = (map[item.stock_id] ?? 0) + item.shares
    }
    return map
  })

  async function refresh() {
    try {
      const payload = await request<{ data: DarknetView }>(`/darknet/state?slot=${colony.slotId}`)
      darknet.value = payload.data
    } catch {
      /* 后端不可用时保留旧视图 */
    }
  }

  async function send(path: string, body: Record<string, unknown>, label: string, log?: (data: any) => string) {
    busy.value = true
    try {
      const payload = await request<{ data: any }>(path, {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, ...body }),
      })
      colony.log(log ? log(payload.data) : `${label}完成`)
      await refresh()
      await colony.refresh({ silent: true })
      return payload.data
    } catch (error) {
      colony.log(`${label}失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
      return null
    } finally {
      busy.value = false
    }
  }

  const trade = (stockId: string, action: 'BUY_LONG' | 'SELL_LONG', shares: number) =>
    send('/darknet/stock/trade', { stock_id: stockId, action, shares }, '股票交易', (d) =>
      `${action === 'BUY_LONG' ? '买入' : '卖出'} ${d.shares} 股 ${d.stock_id} @ ${d.price}（手续费 ${d.fee}）`,
    )

  const openShort = (stockId: string, shares: number, leverage: number) =>
    send('/darknet/short', { stock_id: stockId, shares, leverage }, '做空', (d) =>
      `开空 ${d.shares} 股 ${d.stock_id}（${d.leverage}x，爆仓价 ${d.liquidation_price}，10 分钟到期）`,
    )

  const marketTrade = (itemId: string, side: 'BUY' | 'SELL', qty: number) =>
    send('/darknet/market/trade', { item_id: itemId, side, qty }, '黑市交易', (d) =>
      d.arrives_at
        ? `黑市下单：${d.item_id} ×${d.qty}（5 分钟后空投送达）`
        : `黑市成交：${d.item_id} ×${d.qty}`,
    )

  const postRumor = (title: string, content: string) =>
    send('/darknet/forum/post', { title, content }, '发帖', (d) => {
      const judge = d.judgement
      return `发帖裁判（${d.source}）：${judge.sentiment} 星级 ${judge.persuasiveness}，动量 +${d.momentum_impact}，暴露度 +${judge.suspicion_risk}`
    })

  const rerollId = (customId?: string) =>
    send('/darknet/reroll-id', customId ? { custom_id: customId } : {}, '洗白身份', (d) => `新身份：${d.burner_id}（暴露度归零）`)

  const buyDataEye = () => send('/darknet/data-eye', {}, '购买 4S 数据眼', () => '4S 深度数据眼已接入：可透视趋势强度与二阶预测')

  function startPolling(intervalMs = 6000) {
    if (timer !== null) return
    void refresh()
    timer = window.setInterval(refresh, intervalMs)
    clock = window.setInterval(() => {
      now.value = Math.floor(Date.now() / 1000)
    }, 1000)
  }

  function stopPolling() {
    if (timer !== null) window.clearInterval(timer)
    if (clock !== null) window.clearInterval(clock)
    timer = null
    clock = null
  }

  return {
    darknet,
    busy,
    now,
    holdings,
    refresh,
    trade,
    openShort,
    marketTrade,
    postRumor,
    rerollId,
    buyDataEye,
    startPolling,
    stopPolling,
  }
})
