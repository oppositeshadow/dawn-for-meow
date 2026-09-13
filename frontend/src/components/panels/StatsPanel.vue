<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { Award, Trophy } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import { useStatsStore } from '@/stores/stats'
import { formatAmount, formatDuration } from '@/utils/format'

const stats = useStatsStore()

onMounted(() => stats.startPolling(20000))
onUnmounted(() => stats.stopPolling())

const career = computed(() => stats.career)

const rows = computed(() => [
  { label: '游戏时长', value: formatDuration(career.value?.playtime_seconds ?? 0) },
  { label: '累计猫薄荷', value: formatAmount(career.value?.total_catnip ?? 0) },
  { label: '累计废铁', value: formatAmount(career.value?.total_scrap ?? 0) },
  { label: '累计芯片', value: formatAmount(career.value?.total_chips ?? 0) },
  { label: '累计钛合金', value: formatAmount(career.value?.total_alloys ?? 0) },
  { label: '累计发电', value: `${formatAmount(career.value?.total_kwh ?? 0)} kWh` },
  { label: '累计出生猫口', value: `${career.value?.total_cats_born ?? 0} 只` },
  { label: '单笔做空最佳', value: `${formatAmount(career.value?.best_short_profit ?? 0)} 算力币` },
  { label: '走私流水', value: `${formatAmount(career.value?.smuggling_volume ?? 0)} 算力币` },
  { label: '完成远征', value: `${career.value?.expeditions_completed ?? 0} 次` },
  { label: '挺过轨道轰炸', value: `${career.value?.bombardment_survived ?? 0} 次` },
  {
    label: '最快重建',
    value:
      career.value?.fastest_rebuild_seconds == null
        ? '尚未经历'
        : formatDuration(career.value.fastest_rebuild_seconds),
  },
])
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <Trophy class="h-3.5 w-3.5" />
      <span>生涯与成就馆</span>
      <Badge
        :text="`徽章 ${stats.unlockedCount} / ${career?.achievements_total ?? 0}`"
        :tone="stats.unlockedCount > 0 ? 'accent' : 'dim'"
      />
      <Badge v-if="career?.completed" text="已通关：破晓" tone="warn" />
    </div>

    <div class="flex-1 space-y-3 overflow-auto p-3 text-[12px]">
      <div class="grid grid-cols-2 gap-x-3 gap-y-1 rounded border border-terminal-line/70 p-2">
        <div
          v-for="row in rows"
          :key="row.label"
          class="flex items-center justify-between gap-2 border-b border-terminal-line/40 py-0.5 last:border-none"
        >
          <span class="text-terminal-dim">{{ row.label }}</span>
          <span class="text-terminal-text">{{ row.value }}</span>
        </div>
      </div>

      <div class="space-y-2">
        <div
          v-for="item in stats.sortedAchievements"
          :key="item.achievement_id"
          class="space-y-1 rounded border border-terminal-line/70 p-2"
          :class="item.unlocked ? 'border-terminal-accent/40' : ''"
        >
          <div class="flex items-center gap-2">
            <Award
              class="h-3.5 w-3.5"
              :class="item.unlocked ? 'text-terminal-accent' : 'text-terminal-dim'"
            />
            <span :class="item.unlocked ? 'text-terminal-accent' : 'text-terminal-text'">
              {{ item.name }}
            </span>
            <span class="ml-auto text-[10px] text-terminal-dim">
              {{ formatAmount(item.progress, 0) }} / {{ formatAmount(item.target, 0) }}
            </span>
          </div>
          <p class="text-[10px] text-terminal-dim">{{ item.desc }}</p>
          <div class="h-1 overflow-hidden rounded bg-terminal-line/50">
            <div
              class="h-full"
              :class="item.unlocked ? 'bg-terminal-accent' : 'bg-terminal-warn/70'"
              :style="{ width: `${item.percent}%` }"
            />
          </div>
        </div>
      </div>

      <p class="text-[10px] text-terminal-dim">
        徽章只在首次达标时点亮，之后即使数值回落也不会熄灭；进度每 20 秒随面板刷新一次。
      </p>
    </div>
  </section>
</template>
