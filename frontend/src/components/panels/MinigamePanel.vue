<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { KeyRound, Radio } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import { useMinigameStore } from '@/stores/minigame'

const minigame = useMinigameStore()
const picks = ref<number[]>([1, 1, 1, 1])
const SYMBOLS = ['甲', '乙', '丙', '丁', '戊', '己']

onMounted(() => minigame.startPolling(15000))
onUnmounted(() => minigame.stopPolling())

const cipher = computed(() => minigame.view?.games.find((game) => game.minigame_id === 'cipher_decode'))
const others = computed(() => (minigame.view?.games ?? []).filter((game) => game.minigame_id !== 'cipher_decode'))

async function submit() {
  const result = await minigame.submitGuess([...picks.value])
  if (result?.solved) picks.value = [1, 1, 1, 1]
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <KeyRound class="h-3.5 w-3.5" />
      <span>星球小游戏</span>
      <Badge :text="`情报破译 ${Math.round((minigame.view?.intel_level ?? 0) * 100)}%`" tone="accent" />
      <Badge v-if="cipher?.state" :text="`今日剩余 ${cipher.state.remaining} 次`" />
    </div>

    <div class="flex-1 space-y-3 overflow-auto p-3 text-[12px]">
      <!-- 密电译码 -->
      <div class="space-y-2 rounded border border-terminal-line/70 p-2">
        <div class="flex items-center gap-2">
          <Radio class="h-3.5 w-3.5 text-terminal-accent" />
          <span>密电译码</span>
          <Badge :text="cipher?.planet_unlocked ? '已解锁' : '需解锁三号冰卫星'" :tone="cipher?.planet_unlocked ? 'accent' : 'warn'" />
          <span class="ml-auto text-[10px] text-terminal-dim">
            最好成绩 {{ cipher?.state?.best_attempts || cipher?.best_score || 0 }} 步
          </span>
        </div>
        <p class="text-[10px] text-terminal-dim">
          4 位密钥 / 6 种符号，反馈只有「位置对 / 符号对」两个数字；每天 3 次，失败不扣资源；成功情报破译 +8%
        </p>

        <div class="flex items-center gap-2">
          <select
            v-for="(value, index) in picks"
            :key="index"
            v-model.number="picks[index]"
            class="rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[12px]"
          >
            <option v-for="(symbol, symbolIndex) in SYMBOLS" :key="symbol" :value="symbolIndex + 1">
              {{ symbol }}
            </option>
          </select>
          <button
            class="btn btn-primary px-2 py-0.5"
            :disabled="minigame.busy || !cipher?.planet_unlocked || (cipher?.state?.remaining ?? 0) <= 0"
            @click="submit"
          >
            提交译码
          </button>
        </div>

        <div v-if="(cipher?.state?.guesses.length ?? 0) > 0" class="space-y-0.5">
          <div v-for="(item, index) in cipher?.state?.guesses ?? []" :key="index" class="flex items-center gap-3 text-[11px]">
            <span class="text-terminal-dim">#{{ index + 1 }}</span>
            <span>{{ item.guess.map((value) => SYMBOLS[value - 1]).join(' ') }}</span>
            <span class="text-terminal-accent">位置对 {{ item.exact }}</span>
            <span class="text-terminal-warn">符号对 {{ item.partial }}</span>
          </div>
        </div>
      </div>

      <!-- 其余小游戏：如实标注未落地 -->
      <div v-for="game in others" :key="game.minigame_id" class="rounded border border-terminal-line/60 px-2 py-1.5 text-[11px]">
        <div class="flex items-center gap-2">
          <span>{{ game.name }}</span>
          <Badge :text="game.planet_unlocked ? '星球已解锁' : '未解锁'" :tone="game.planet_unlocked ? 'dim' : 'warn'" />
          <Badge text="玩法开发中" tone="warn" />
        </div>
        <p class="mt-0.5 text-[10px] text-terminal-dim">
          {{ game.planet_name }}｜{{ game.core_loop }}｜最好成绩 {{ game.best_score }}
        </p>
      </div>

      <p class="border-t border-terminal-line pt-2 text-[10px] text-terminal-dim">{{ minigame.view?.note }}</p>
    </div>
  </section>
</template>
