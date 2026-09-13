<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { Scroll } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import { useDoctrineStore } from '@/stores/doctrine'

const doctrine = useDoctrineStore()
let timer: number | null = null

onMounted(() => {
  void doctrine.refresh()
  timer = window.setInterval(() => void doctrine.refresh(), 15000)
})
onUnmounted(() => {
  if (timer !== null) window.clearInterval(timer)
})

/** 该政令的效果里有没有"已接线"的（后端 wired_effects 是唯一出处） */
function wired(item: { effects: Record<string, number> }): boolean {
  const wiredKeys = doctrine.view?.wired_effects ?? []
  return Object.keys(item.effects).some((key) => wiredKeys.includes(key))
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <Scroll class="h-3.5 w-3.5" />
      <span>星系法典</span>
      <Badge :text="`已点亮 ${doctrine.view?.unlocked_count ?? 0}/${doctrine.view?.total ?? 8}`" tone="accent" />
      <span class="ml-auto text-[10px]">
        文明凝聚力 {{ (doctrine.view?.unity ?? 0).toFixed(1) }}
      </span>
    </div>

    <div class="flex-1 space-y-2 overflow-auto p-3 text-[12px]">
      <p class="text-[10px] text-terminal-dim">
        凝聚力由【文明呼噜大师】产出（每只 +0.02/s）；政令全星系生效、点亮后不可退点。
      </p>
      <div
        v-for="item in doctrine.view?.doctrines ?? []"
        :key="item.doctrine_id"
        class="space-y-1 rounded border p-2"
        :class="item.unlocked ? 'border-terminal-accent/40' : 'border-terminal-line/70'"
      >
        <div class="flex items-center gap-2">
          <span :class="item.unlocked ? 'text-terminal-accent' : 'text-terminal-text'">{{ item.name }}</span>
          <Badge v-if="item.unlocked" text="已点亮" tone="accent" />
          <Badge v-else-if="!wired(item)" text="效果待接线（可点亮）" tone="warn" />
          <button
            v-if="!item.unlocked"
            class="btn ml-auto px-2 py-0.5"
            :disabled="doctrine.busy || !item.affordable"
            @click="doctrine.unlock(item.doctrine_id)"
          >
            {{ item.cost }} 凝聚力
          </button>
        </div>
        <p class="text-[10px] text-terminal-dim">{{ item.effect_text }}</p>
      </div>
    </div>
  </section>
</template>
