<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RadioTower, TrendingDown, TrendingUp, Send, ShieldOff } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import GaugeBar from '@/components/common/GaugeBar.vue'
import { useDarknetStore } from '@/stores/darknet'

const darknet = useDarknetStore()
const shares = ref(10)
const leverage = ref(2)
const postTitle = ref('')
const postContent = ref('')

onMounted(() => darknet.startPolling(6000))
onUnmounted(() => darknet.stopPolling())

const view = computed(() => darknet.darknet)
const bandLabel: Record<string, string> = {
  CLEAN: '干净',
  SURCHARGED: '手续费 ×1.5',
  INTERROGATION: '图灵质询',
  TRACKED: '被挂信标',
  BANNED: '已封号',
}

function trendTone(trend: number): 'accent' | 'warn' | 'dim' {
  if (trend > 5) return 'accent'
  if (trend < -5) return 'warn'
  return 'dim'
}

function cooldownText(endsAt: number): string {
  const left = Math.max(0, endsAt - darknet.now)
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`
}

async function submitPost() {
  if (!postTitle.value || !postContent.value) return
  const ok = await darknet.postRumor(postTitle.value, postContent.value)
  if (ok) {
    postTitle.value = ''
    postContent.value = ''
  }
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <RadioTower class="h-3.5 w-3.5" />
      <span>幽灵总线 · 深网</span>
      <Badge :text="`⚡ ${(view?.byte_credits ?? 0).toFixed(1)}`" tone="accent" />
      <Badge :text="`暴露 ${(view?.exposure ?? 0).toFixed(0)}｜${bandLabel[view?.exposure_band ?? 'CLEAN']}`" :tone="(view?.exposure ?? 0) >= 50 ? 'crit' : 'dim'" />
      <Badge v-if="view?.has_4s_data" text="4S 数据眼" tone="warn" />
    </div>

    <div class="flex-1 space-y-3 overflow-auto p-3 text-[12px]">
      <!-- 行情 -->
      <div class="space-y-1">
        <div class="flex items-center gap-2 text-[10px] text-terminal-dim">
          <span>代号 {{ view?.burner_id }}</span>
          <button class="btn ml-auto px-1.5 py-0" :disabled="darknet.busy" @click="darknet.rerollId()">
            <ShieldOff class="mr-1 inline h-3 w-3" />洗白（10 废铁 + 50 币）
          </button>
          <button v-if="!view?.has_4s_data" class="btn px-1.5 py-0" :disabled="darknet.busy" @click="darknet.buyDataEye()">
            4S 数据眼（500 币）
          </button>
        </div>
        <div v-for="quote in view?.stocks ?? []" :key="quote.stock_id" class="rounded border border-terminal-line/70 px-2 py-1.5">
          <div class="flex items-center gap-2">
            <span class="w-24">{{ quote.stock_id }}</span>
            <span class="tabular-nums">{{ quote.price.toFixed(2) }}</span>
            <Badge :text="`趋势 ${quote.trend_strength.toFixed(0)}`" :tone="trendTone(quote.trend_strength)" />
            <span class="text-[10px] text-terminal-dim">
              买 {{ quote.ask.toFixed(2) }} / 卖 {{ quote.bid.toFixed(2) }}
              <template v-if="view?.has_4s_data">｜二阶 {{ (quote.target_forecast * 100).toFixed(0) }}%</template>
            </span>
            <span class="ml-auto flex items-center gap-1">
              <input v-model.number="shares" type="number" min="1" class="w-12 rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]" />
              <button class="btn px-1.5 py-0" :disabled="darknet.busy" @click="darknet.trade(quote.stock_id, 'BUY_LONG', shares)">
                <TrendingUp class="h-3 w-3" />
              </button>
              <button class="btn px-1.5 py-0" :disabled="darknet.busy || !darknet.holdings[quote.stock_id]" @click="darknet.trade(quote.stock_id, 'SELL_LONG', shares)">
                <TrendingDown class="h-3 w-3" />
              </button>
              <select v-model.number="leverage" class="rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]">
                <option v-for="value in 10" :key="value" :value="value">{{ value }}x</option>
              </select>
              <button class="btn px-1.5 py-0" :disabled="darknet.busy" @click="darknet.openShort(quote.stock_id, shares, leverage)">空</button>
            </span>
          </div>
          <div class="mt-1 flex items-center gap-2 text-[10px] text-terminal-dim">
            <span>持仓 {{ (darknet.holdings[quote.stock_id] ?? 0).toFixed(0) }} 股</span>
          </div>
        </div>
      </div>

      <!-- 做空合约 -->
      <div v-if="(view?.short_contracts.length ?? 0) > 0" class="space-y-1 border-t border-terminal-line pt-2">
        <div class="text-[11px] text-terminal-dim">做空合约（10 分钟到期强制交割）</div>
        <div v-for="(contract, index) in view?.short_contracts ?? []" :key="index" class="flex items-center gap-2 text-[11px]">
          <span>{{ contract.stock_id }} ×{{ contract.shares }}（{{ contract.leverage }}x）</span>
          <span class="text-terminal-dim">爆仓价 {{ contract.liquidation_price.toFixed(2) }}</span>
          <span class="ml-auto text-terminal-dim">交割 {{ cooldownText(contract.ends_at) }}</span>
        </div>
      </div>

      <!-- 黑市 -->
      <div class="space-y-1 border-t border-terminal-line pt-2">
        <div class="text-[11px] text-terminal-dim">匿名死斗黑市（空投 5 分钟送达）</div>
        <div v-for="(item, key) in view?.market_items ?? {}" :key="key" class="flex items-center gap-2 text-[11px]">
          <span>{{ item.name }}</span>
          <span class="text-terminal-dim">{{ item.current_price.toFixed(1) }} 币/份（{{ item.unit }} {{ item.resource }}）</span>
          <span class="ml-auto flex gap-1">
            <button class="btn px-1.5 py-0" :disabled="darknet.busy" @click="darknet.marketTrade(key, 'BUY', 1)">买</button>
            <button class="btn px-1.5 py-0" :disabled="darknet.busy" @click="darknet.marketTrade(key, 'SELL', 1)">卖</button>
          </span>
        </div>
        <div class="flex flex-wrap gap-1 pt-1">
          <button
            v-for="(item, key) in view?.contraband ?? {}"
            :key="key"
            class="btn px-1.5 py-0"
            :disabled="darknet.busy"
            @click="darknet.marketTrade(key, 'BUY', 1)"
          >
            {{ item.name }}（{{ item.price }} 币）
          </button>
        </div>
        <div v-for="(item, index) in view?.pending_deliveries ?? []" :key="index" class="text-[10px] text-terminal-dim">
          在途：{{ item.name }} ×{{ item.qty }} · {{ cooldownText(item.ends_at) }}
        </div>
      </div>

      <!-- 论坛 -->
      <div class="space-y-1 border-t border-terminal-line pt-2">
        <div class="flex items-center gap-2 text-[11px] text-terminal-dim">
          <span>匿名 BBS（不显示 IP / 网段）</span>
          <span v-if="(view?.post_cooldown_left ?? 0) > 0" class="text-terminal-warn">
            冷却 {{ cooldownText(darknet.now + (view?.post_cooldown_left ?? 0)) }}
          </span>
        </div>
        <div class="space-y-1">
          <input v-model="postTitle" placeholder="标题" class="w-full rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]" />
          <textarea v-model="postContent" rows="2" placeholder="自由造谣做空/拉多……" class="w-full rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]" />
          <button
            class="btn btn-primary px-2 py-0.5"
            :disabled="darknet.busy || !postTitle || !postContent || (view?.post_cooldown_left ?? 0) > 0"
            @click="submitPost"
          >
            <Send class="mr-1 inline h-3 w-3" />发帖做局（LLM 裁判）
          </button>
        </div>
        <div v-for="post in view?.forum ?? []" :key="post.id" class="rounded border border-terminal-line/60 px-2 py-1 text-[11px]">
          <div class="flex items-center gap-2 text-[10px] text-terminal-dim">
            <span>{{ post.author_id }}{{ post.is_player ? '（你）' : '' }}</span>
            <span>发帖 {{ post.author_post_count }}</span>
            <span>命中率 {{ (post.author_hit_rate * 100).toFixed(0) }}%</span>
            <Badge v-if="post.telemetry_code" :text="`遥测码 ${post.telemetry_code}`" />
            <Badge v-if="post.persuasiveness" :text="`${post.sentiment ?? 'NEUTRAL'} ${post.persuasiveness}★`" tone="warn" />
          </div>
          <div>{{ post.content }}</div>
        </div>
      </div>
    </div>
  </section>
</template>
