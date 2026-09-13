<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { KeyRound, Radio } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import { useMinigameStore } from '@/stores/minigame'

const minigame = useMinigameStore()
const picks = ref<number[]>([1, 1, 1, 1])
const mix = ref<number[]>([40, 30, 30])
const SYMBOLS = ['甲', '乙', '丙', '丁', '戊', '己']

onMounted(() => minigame.startPolling(15000))
onUnmounted(() => minigame.stopPolling())

const cipher = computed(() => minigame.view?.games.find((game) => game.minigame_id === 'cipher_decode'))
// 已落地的三个玩法各有专属界面；将来新增的小游戏会先在这里以"开发中"出现
const others = computed(() =>
  (minigame.view?.games ?? []).filter(
    (game) => !['cipher_decode', 'vein_scan', 'forge_recipe'].includes(game.minigame_id),
  ),
)
const vein = computed(() => minigame.view?.games.find((game) => game.minigame_id === 'vein_scan'))
const forge = computed(() => minigame.view?.games.find((game) => game.minigame_id === 'forge_recipe'))
const veinCells = computed(() => {
  const size = vein.value?.state?.board_size ?? 8
  const revealed = new Map((vein.value?.state?.revealed ?? []).map((item) => [`${item.x},${item.y}`, item.hint]))
  return Array.from({ length: size * size }, (_, index) => {
    const x = index % size
    const y = Math.floor(index / size)
    const hint = revealed.get(`${x},${y}`)
    return { x, y, revealed: hint !== undefined, hint: hint ?? 0 }
  })
})

function mixTotal(): number {
  return mix.value.reduce((sum, value) => sum + (Number(value) || 0), 0)
}

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

      <!-- 矿脉扫描 -->
      <div v-if="vein" class="space-y-2 rounded border border-terminal-line/70 p-2">
        <div class="flex items-center gap-2">
          <span>⛏️ 矿脉扫描</span>
          <Badge :text="vein.planet_unlocked ? '已解锁' : '需解锁四号小行星带'" :tone="vein.planet_unlocked ? 'accent' : 'warn'" />
          <span class="ml-auto text-[10px] text-terminal-dim">
            配额 {{ vein.state?.quota }}/{{ vein.state?.quota_max }} · 本盘已找 {{ vein.state?.found_count }}/{{ vein.state?.vein_count }}
          </span>
        </div>
        <p class="text-[10px] text-terminal-dim">
          扫雷式 8×8 推理探矿：数字 = 相邻 8 格矿脉数；扫空只消耗配额；每 30 分钟回 1 次
          <template v-if="(vein.state?.next_regen_in ?? 0) > 0">
            （下 1 次 {{ Math.ceil((vein.state?.next_regen_in ?? 0) / 60) }} 分钟后）
          </template>
        </p>
        <div class="grid grid-cols-8 gap-1">
          <button
            v-for="cell in veinCells"
            :key="`${cell.x}-${cell.y}`"
            class="flex h-7 items-center justify-center rounded border text-[11px] transition-colors disabled:cursor-not-allowed"
            :class="!cell.revealed
              ? 'border-terminal-line text-terminal-dim hover:border-terminal-accent/60'
              : cell.hint === -1
                ? 'border-terminal-accent bg-terminal-accent/15 text-terminal-accent'
                : 'border-terminal-line/60 text-terminal-text'"
            :disabled="minigame.busy || !vein?.planet_unlocked || cell.revealed || (vein?.state?.quota ?? 0) <= 0"
            @click="minigame.scanVein(cell.x, cell.y)"
          >
            <template v-if="!cell.revealed">·</template>
            <template v-else-if="cell.hint === -1">矿</template>
            <template v-else>{{ cell.hint }}</template>
          </button>
        </div>
      </div>

      <!-- 熔炉配比 -->
      <div v-if="forge" class="space-y-2 rounded border border-terminal-line/70 p-2">
        <div class="flex items-center gap-2">
          <span>🔥 熔炉配比</span>
          <Badge :text="forge.planet_unlocked ? '已解锁' : '需解锁二号熔岩星'" :tone="forge.planet_unlocked ? 'accent' : 'warn'" />
          <span class="ml-auto text-[10px] text-terminal-dim">
            配方 {{ forge.state?.recipes_found }}/{{ forge.state?.recipe_cap }} · 熔炼 +{{ Math.round((forge.state?.smelt_speed_bonus ?? 0) * 100) }}%
          </span>
        </div>
        <p class="text-[10px] text-terminal-dim">
          按比例投料逼近隐藏配方，反馈只有「太热／太冷／比例偏差」；每轮消耗 {{ forge.state?.scrap_cost ?? 20 }} 废铁，每找到 1 条永久 +5% 熔炼速度
        </p>
        <div class="flex items-center gap-2">
          <input
            v-for="(value, index) in mix"
            :key="index"
            v-model.number="mix[index]"
            type="number"
            min="0"
            max="100"
            class="w-14 rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]"
          />
          <span class="text-[10px]" :class="mixTotal() === 100 ? 'text-terminal-accent' : 'text-terminal-dim'">合计 {{ mixTotal() }}</span>
          <button
            class="btn btn-primary px-2 py-0.5"
            :disabled="minigame.busy || !forge?.planet_unlocked"
            @click="minigame.submitMix([...mix])"
          >
            投料
          </button>
        </div>
        <div v-if="forge.state?.last_feedback" class="text-[11px]" :class="forge.state.last_feedback.result === 'HIT' ? 'text-terminal-accent' : 'text-terminal-warn'">
          上次反馈：{{ forge.state.last_feedback.hint }}
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
