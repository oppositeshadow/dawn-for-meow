<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Radio, Sparkles } from 'lucide-vue-next'

import { api } from '@/api/client'
import { useColonyStore } from '@/stores/colony'
import type { RadioItem } from '@/types/game'

/**
 * 常驻底栏公频电台（模块 M）。
 *
 * * 数据来源：`GET /radio/feed`（event_templates 表 + 本地狂欢节填槽，0 Token）；
 * * 兜底：后端不可用时退回组件内置台词，底栏永不空；
 * * 「生成语料」按钮触发 `POST /radio/generate`（LLM 场景 3：一次 6 条，走便宜小模型，
 *   失败/超预算自动回本地语料池，玩家零感知）。
 */
const colony = useColonyStore()

const LOCAL_LINES = [
  '公频 07：地下避难所自检完成，通风口冷凝水回收率 61%。',
  '匿名 BBS 传闻：熔岩重工的高炉又炸膛了……真假自辨。',
  '拾荒电台：市中心电脑城的货架还有一半没被翻过。',
  '猫猫气象台：地表辐射尘埃下降 3%，适合夜里出勤。',
  '不明信号：有人在废弃超市货架后面听见了呼噜声。',
]

const index = ref(0)
const items = ref<RadioItem[]>([])
const poolSize = ref(0)
const generating = ref(false)
const genNote = ref<string | null>(null)
let timer: number | null = null

onMounted(() => {
  timer = window.setInterval(() => {
    index.value += 1
  }, 8000)
  void loadFeed()
})

onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})

const latestLog = computed(() => colony.logs[0]?.text ?? null)
const current = computed(() =>
  items.value.length ? items.value[index.value % items.value.length] : null,
)
const line = computed(
  () => latestLog.value ?? current.value?.text ?? LOCAL_LINES[index.value % LOCAL_LINES.length],
)
const categoryText = computed(() => {
  if (!current.value || latestLog.value) return '公频 07'
  const labels: Record<string, string> = {
    RADIO_NEWS: '公频 07 · 新闻',
    BBS_POST: '匿名 BBS',
    DISASTER_ALERT: '天网告警',
  }
  return labels[current.value.category] ?? '公频 07'
})

async function loadFeed() {
  try {
    const feed = await api.radioFeed(colony.slotId, 12)
    items.value = feed.data.items
    poolSize.value = feed.data.pool_size
  } catch {
    /* 后端不可用时保持本地台词 */
  }
}

async function generate() {
  generating.value = true
  genNote.value = null
  try {
    const result = await api.radioGenerate({ slot: colony.slotId, count: 6 })
    const data = result.data
    genNote.value =
      data.source === 'LLM'
        ? `星区推演导演新写 ${data.generated} 条${data.rejected ? `（${data.rejected} 条不合规已丢弃）` : ''}`
        : (data.note ?? '本地语料池保持')
    colony.log(`公频语料：${genNote.value}`, data.source === 'LLM' ? 'info' : 'warn')
    await loadFeed()
  } catch {
    genNote.value = '生成失败，稍后再试'
  } finally {
    generating.value = false
    window.setTimeout(() => {
      genNote.value = null
    }, 6000)
  }
}
</script>

<template>
  <footer class="panel flex items-center gap-3 px-3 py-1.5 text-[11px] text-terminal-dim">
    <Radio class="h-3.5 w-3.5 shrink-0 text-terminal-accent" />
    <span class="whitespace-nowrap text-terminal-accent">{{ categoryText }}</span>
    <span class="truncate">{{ line }}</span>
    <span v-if="genNote" class="whitespace-nowrap text-terminal-accent">{{ genNote }}</span>
    <span class="ml-auto flex shrink-0 items-center gap-2 whitespace-nowrap">
      <button class="btn px-1.5 py-0.5" :disabled="generating" @click="generate">
        <Sparkles class="mr-1 inline h-3 w-3" />
        {{ generating ? '推演中…' : '生成语料' }}
      </button>
      <span>语料池 {{ poolSize }} ·</span>
      <span>
        快照
        {{ colony.lastSyncAt ? new Date(colony.lastSyncAt).toLocaleTimeString('zh-CN', { hour12: false }) : '—' }}
      </span>
    </span>
  </footer>
</template>
