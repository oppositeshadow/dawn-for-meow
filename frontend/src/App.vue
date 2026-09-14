<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Hammer, Orbit, Pickaxe, RefreshCw, Sparkles, Terminal } from 'lucide-vue-next'

import Modal from '@/components/common/Modal.vue'
import Badge from '@/components/common/Badge.vue'
import ResourceBar from '@/components/hud/ResourceBar.vue'
import ThreatBar from '@/components/hud/ThreatBar.vue'
import FacilitiesPanel from '@/components/panels/FacilitiesPanel.vue'
import TechPanel from '@/components/panels/TechPanel.vue'
import WarRoomPanel from '@/components/panels/WarRoomPanel.vue'
import GardenPanel from '@/components/panels/GardenPanel.vue'
import DarknetPanel from '@/components/panels/DarknetPanel.vue'
import MinigamePanel from '@/components/panels/MinigamePanel.vue'
import StatsPanel from '@/components/panels/StatsPanel.vue'
import SavePanel from '@/components/panels/SavePanel.vue'
import PlanetPanel from '@/components/panels/PlanetPanel.vue'
import DoctrinePanel from '@/components/panels/DoctrinePanel.vue'
import ProductionPanel from '@/components/panels/ProductionPanel.vue'
import RadioTicker from '@/components/radio/RadioTicker.vue'
import { useAutoSave } from '@/composables/useAutoSave'
import { useGameLoop } from '@/composables/useGameLoop'
import { useColonyStore } from '@/stores/colony'
import { useFacilitiesStore } from '@/stores/facilities'
import { usePlanetStore } from '@/stores/planet'
import { RESOURCE_LABELS, formatDuration } from '@/utils/format'

const colony = useColonyStore()
const facilitiesStore = useFacilitiesStore()
const planetStore = usePlanetStore()

useGameLoop()
useAutoSave()

const offlineOpen = ref(false)
const leftTab = ref<
  'facilities' | 'tech' | 'garden' | 'darknet' | 'minigame' | 'stats' | 'planet' | 'doctrine' | 'save'
>('facilities')
const leftTabs = [
  { key: 'facilities' as const, label: '设施建造' },
  { key: 'tech' as const, label: '科技树' },
  { key: 'garden' as const, label: '猫草实验室' },
  { key: 'darknet' as const, label: '智械深网' },
  { key: 'minigame' as const, label: '小游戏' },
  { key: 'stats' as const, label: '生涯成就' },
  { key: 'planet' as const, label: '星区星图' },
  { key: 'doctrine' as const, label: '星系法典' },
  { key: 'save' as const, label: '存档槽位' },
]

const coldStartSteps: Record<string, { title: string; body: string; action: string; run: () => void }> = {
  GATHER: {
    title: '① 唤醒与手工摸索',
    body: '通风管道的尽头就是废弃的地表建筑废墟。手点翻找 5 次，就能攒够第一个纸箱窝的废铁。',
    action: '翻检地表建筑废墟',
    run: () => void colony.scavenge(),
  },
  FIRST_FARM: {
    title: '② 第一座纸箱窝已就位',
    body: '折耳猫在纸箱里打呼噜了。继续翻废墟攒够 10 废铁，造一座【水培农田】——那是猫草农夫的工位来源。',
    action: '建造水培农田（10 废铁）',
    run: () => void colony.build('farm_plot'),
  },
  ASSIGN_FARMER: {
    title: '③ 把猫猫派去当农夫',
    body: '空闲的折耳猫还在打呼噜。派它去水培农田，猫薄荷就会自动产出 +0.2/s。',
    action: '指派 1 只农夫猫',
    run: () => void colony.dispatch('farmer', 1),
  },
  AUTOMATE_SCRAP: {
    title: '④ 让废铁也自动起来',
    body: '造一座【废品解体操作台】，它提供 2 个拾荒猫工位——有了工位，新猫猫就能自动产废铁了。',
    action: '建造废品解体操作台（8 废铁）',
    run: () => void colony.build('scavenge_station'),
  },
  SECOND_BOX: {
    title: '⑤ 给第二只猫留个位置',
    body:
      '只有 1 座纸箱窝时，猫口正好等于承载力 K——逻辑斯蒂繁育的增长为 0，' +
      '第二只猫永远不会来。再花 6 废铁造一座【瓦楞纸箱窝】，K 变成 2，猫口才会继续长。',
    action: '建造第二座纸箱窝（6 废铁）',
    run: () => void colony.build('housing_box'),
  },
  ASSIGN_SCAVENGER: {
    title: '⑥ 派猫猫去捡废铁',
    body: '第 2 只猫已经住进来了，把它派到【废品解体操作台】，机械废铁就会以 0.5/s 自动累积。',
    action: '指派 1 只拾荒猫',
    run: () => void colony.dispatch('scavenger', 1),
  },
}

const currentStep = computed(() => coldStartSteps[colony.coldStartPhase] ?? null)

// 冷启动期的即时目标：让"点废墟"这件事有明确的进度感（每阶段只关心当前要造的东西）
const coldStartGoal = computed(() => {
  const scrap = Math.floor(colony.resources.scrap)
  const goals: Record<string, { label: string; need: number }> = {
    GATHER: { label: '瓦楞纸箱窝', need: 5 },
    FIRST_FARM: { label: '水培农田', need: 10 },
    AUTOMATE_SCRAP: { label: '废品解体操作台', need: 8 },
  }
  const goal = goals[colony.coldStartPhase]
  if (!goal) return null
  return {
    label: goal.label,
    have: scrap,
    need: goal.need,
    percent: Math.min(100, Math.round((scrap / goal.need) * 100)),
    done: scrap >= goal.need,
  }
})

// 外星球开荒引导：切到外星球后基地是空的（按 §15.3 初始资源/猫口全 0），
// 母星那套"手点废墟"的冷启动流程在这里不适用 ⇒ 单独给一段指引。
const isStarPlanet = computed(() => colony.planetId !== 0)
const starName = computed(
  () => planetStore.planets.find((item) => item.planet_id === colony.planetId)?.name ?? `星球 ${colony.planetId}`,
)
const starColonyEmpty = computed(
  () =>
    isStarPlanet.value &&
    colony.population.total === 0 &&
    (colony.facilities.housing_box ?? 0) === 0,
)

// 当前星球的性格（承载 / 产粮 / 专属产出），文案来自后端 planet_traits
const starTraits = computed(
  () => planetStore.planets.find((item) => item.planet_id === colony.planetId)?.traits ?? null,
)
const starTraitText = computed(() => {
  const traits = starTraits.value
  if (!traits) return ''
  const parts = [`承载力 ×${traits.capacity_multiplier}`, `产粮 ×${traits.catnip_multiplier}`]
  for (const [resource, factor] of Object.entries(traits.output_bonus ?? {})) {
    if (factor !== 1) {
      parts.push(`${resource === 'scrap' ? '废铁' : resource === 'chips' ? '芯片' : resource} ×${factor}`)
    }
  }
  return parts.join(' ｜ ')
})

// 外星球开荒清单：猫口到了之后接着干什么（母星冷启动的"外星球版"，去掉手点废墟那几步）
const starSteps = computed(() => {
  const facilities = colony.facilities
  const jobs = colony.workstations
  const traits = starTraits.value
  const farm = {
    label: '造水培农田（10 废铁）',
    hint: traits && traits.catnip_multiplier < 1 ? `本地产粮 ×${traits.catnip_multiplier}，先解决吃饭` : undefined,
    done: (facilities.farm_plot ?? 0) > 0,
    action: '建造水培农田',
    run: () => void colony.build('farm_plot'),
  }
  const farmer = {
    label: '派 1 只农夫猫上工',
    hint: undefined,
    done: (jobs.farmer ?? 0) > 0,
    action: '指派农夫',
    run: () => void colony.dispatch('farmer', 1),
  }
  const station = {
    label: '造废品解体操作台（8 废铁）',
    hint:
      traits && (traits.output_bonus.scrap > 1 || traits.output_bonus.chips > 1)
        ? `本地废铁 ×${traits.output_bonus.scrap}、芯片 ×${traits.output_bonus.chips}，先铺产出`
        : undefined,
    done: (facilities.scavenge_station ?? 0) > 0,
    action: '建造操作台',
    run: () => void colony.build('scavenge_station'),
  }
  // 推荐顺序按星球性格走：挖矿有加成 ⇒ 先铺产出；否则按母星那套先温饱后产出
  const mineFirst = traits ? traits.output_bonus.scrap > 1 || traits.output_bonus.chips > 1 : false
  const afterHousing = mineFirst ? [station, farm, farmer] : [farm, farmer, station]
  return [
    {
      label: '把猫口运过来',
      hint: undefined,
      done: colony.population.total > 0,
      action: '去星区星图运猫',
      run: () => {
        leftTab.value = 'planet'
      },
    },
    {
      label: '盖第一座纸箱窝（5 废铁）',
      hint: undefined,
      done: (facilities.housing_box ?? 0) > 0,
      action: '建造纸箱窝',
      run: () => void colony.build('housing_box'),
    },
    ...afterHousing,
  ]
})

const nextStarStep = computed(() => starSteps.value.find((step) => !step.done) ?? null)

const offlineSummary = computed(() => {
  const report = colony.offlineReport
  if (!report) return null
  return {
    duration: formatDuration(report.elapsed_seconds),
    catnip: report.gained_catnip,
    scrap: report.gained_scrap,
    cats: report.gained_cats,
    alloys: report.gained_alloys ?? 0,
    batches: report.smelted_batches ?? 0,
    unity: report.gained_unity ?? 0,
    research: report.research ?? null,
    starved: report.is_starved,
    capped: report.is_capped,
    overflowed: report.overflowed_resources,
    clockAnomaly: report.clock_anomaly,
    notes: report.notes,
  }
})

async function bootstrap() {
  await facilitiesStore.loadDefinitions()
  await colony.refresh({ silent: true })
  await planetStore.refresh() // 外星球引导要显示星球名
  // 只有"真的离开过"才弹《离线休整报表》（验收清单 A-1：关闭页面一小时后重进）；
  // 页面刷新这类几秒钟的空档不打扰玩家。
  if ((colony.offlineReport?.elapsed_seconds ?? 0) >= 60) offlineOpen.value = true
}

onMounted(bootstrap)
</script>

<template>
  <div class="flex h-full flex-col gap-2 p-2">
    <ResourceBar />
    <ThreatBar />

    <main class="grid min-h-0 flex-1 grid-cols-12 gap-2">
      <div class="col-span-4 flex min-h-0 flex-col gap-2">
        <div class="flex gap-1">
          <button
            v-for="tab in leftTabs"
            :key="tab.key"
            class="btn px-2 py-0.5"
            :class="leftTab === tab.key ? 'btn-primary' : ''"
            @click="leftTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>
        <FacilitiesPanel v-if="leftTab === 'facilities'" class="min-h-0 flex-1" />
        <TechPanel v-else-if="leftTab === 'tech'" class="min-h-0 flex-1" />
        <GardenPanel v-else-if="leftTab === 'garden'" class="min-h-0 flex-1" />
        <DarknetPanel v-else-if="leftTab === 'darknet'" class="min-h-0 flex-1" />
        <MinigamePanel v-else-if="leftTab === 'minigame'" class="min-h-0 flex-1" />
        <StatsPanel v-else-if="leftTab === 'stats'" class="min-h-0 flex-1" />
        <PlanetPanel v-else-if="leftTab === 'planet'" class="min-h-0 flex-1" />
        <DoctrinePanel v-else-if="leftTab === 'doctrine'" class="min-h-0 flex-1" />
        <SavePanel v-else class="min-h-0 flex-1" />
      </div>

      <section class="panel col-span-5 flex min-h-0 flex-col">
        <div class="panel-title justify-between">
          <span class="flex items-center gap-2">
            <Terminal class="h-3.5 w-3.5" />
            中央终端
          </span>
          <span class="flex items-center gap-2">
            <Badge :text="colony.loaded ? '已连接后端' : '连接中…'" :tone="colony.loaded ? 'accent' : 'warn'" />
            <button class="btn border-none px-1" :disabled="colony.busy" @click="colony.refresh()">
              <RefreshCw class="h-3 w-3" />
            </button>
          </span>
        </div>

        <!-- 外星球开荒引导（优先于母星冷启动步骤：母星那套"手点废墟"在外星球不适用） -->
        <div v-if="starColonyEmpty" class="border-b border-terminal-line bg-terminal-warn/5 p-4">
          <div class="flex items-center gap-2 text-[13px] text-terminal-warn">
            <Orbit class="h-4 w-4" />
            登陆【{{ starName }}】· 这里还是一片空地
          </div>
          <p class="mt-2 text-[12px] leading-relaxed text-terminal-text/90">
            外星球不会自动长猫——按《数值平衡表》§15.3，新基地的初始资源、设施与猫口全是 0。
            先在星图上从母星运一批猫口过来（出发即离港、单趟 60 秒），它们落地后才会开始盖窝、生产。
          </p>
          <p class="mt-2 text-[11px] leading-relaxed text-terminal-dim">
            星球性格：{{ starTraitText || '与母星一致' }} —— 先派 2~4 只探路，别把主巢搬空。
          </p>
          <div class="mt-3 flex gap-2">
            <button class="btn btn-primary flex items-center gap-2 px-3 py-1.5" @click="leftTab = 'planet'">
              <Orbit class="h-3.5 w-3.5" />
              去星区星图运猫
            </button>
            <button class="btn px-3 py-1.5" @click="planetStore.switchTo(0)">回母星调度</button>
          </div>
        </div>
        <!-- 外星球开荒清单：猫口落地后按母星同一条路径重走一遍（不含手点废墟） -->
        <div v-else-if="isStarPlanet" class="border-b border-terminal-line bg-terminal-accent/5 p-4">
          <div class="flex items-center gap-2 text-[13px] text-terminal-accent">
            <Orbit class="h-4 w-4" />
            【{{ starName }}】开荒进度
          </div>
          <ul class="mt-2 space-y-1 text-[12px]">
            <li v-if="starTraitText" class="text-[10px] text-terminal-dim">星球性格：{{ starTraitText }}</li>
            <li v-for="step in starSteps" :key="step.label" class="flex items-center gap-2">
              <span :class="step.done ? 'text-terminal-accent' : 'text-terminal-dim'">
                {{ step.done ? '☑' : '☐' }}
              </span>
              <span :class="step.done ? 'text-terminal-dim line-through' : 'text-terminal-text'">
                {{ step.label }}
              </span>
              <span v-if="step.hint && !step.done" class="text-[10px] text-terminal-warn/80">{{ step.hint }}</span>
            </li>
          </ul>
          <button
            v-if="nextStarStep"
            class="btn btn-primary mt-3 px-3 py-1.5"
            :disabled="colony.busy"
            @click="nextStarStep.run()"
          >
            {{ nextStarStep.action }}
          </button>
          <p v-else class="mt-3 text-[11px] text-terminal-dim">
            这颗星球的自动化闭环已跑通，可以回母星或继续铺产能了。
          </p>
        </div>
        <div v-else-if="currentStep" class="border-b border-terminal-line bg-terminal-accent/5 p-4">
            <div class="flex items-center gap-2 text-[13px] text-terminal-accent">
            <Sparkles class="h-4 w-4" />
            {{ currentStep.title }}
          </div>
          <p class="mt-2 text-[12px] leading-relaxed text-terminal-text/90">{{ currentStep.body }}</p>
          <button
            class="btn btn-primary mt-3 flex items-center gap-2 px-3 py-1.5"
            :disabled="colony.busy"
            @click="currentStep.run()"
          >
            <Pickaxe class="h-3.5 w-3.5" />
            {{ currentStep.action }}
          </button>
          <button
            class="btn ml-2 mt-3 px-3 py-1.5"
            :disabled="colony.busy"
            @click="colony.scavenge()"
          >
            翻检地表建筑废墟（+1 废铁）
          </button>
          <p v-if="colony.manualClicksLeft !== null" class="mt-2 text-[10px] text-terminal-dim">
            手点废墟剩余次数：{{ colony.manualClicksLeft }}
          </p>
          <!-- 即时目标：把"点到什么时候是个头"讲清楚 -->
          <div v-if="coldStartGoal" class="mt-2 space-y-1">
            <div class="flex items-center justify-between text-[10px]">
              <span :class="coldStartGoal.done ? 'text-terminal-accent' : 'text-terminal-dim'">
                目标：{{ coldStartGoal.label }}（废铁 {{ coldStartGoal.have }} / {{ coldStartGoal.need }}）
              </span>
              <span class="text-terminal-dim">{{ coldStartGoal.percent }}%</span>
            </div>
            <div class="h-1 overflow-hidden rounded bg-terminal-line/50">
              <div
                class="h-full bg-terminal-accent"
                :style="{ width: `${coldStartGoal.percent}%` }"
              />
            </div>
          </div>
        </div>
        <div v-else class="border-b border-terminal-line px-4 py-3 text-[12px] text-terminal-dim">
          开局闭环已完成：农夫产粮、拾荒猫产铁，双手彻底解放，可以安心挂机。
          <span class="text-terminal-dim/80">
            下一步：猫口顶到承载力 K 时再造【瓦楞纸箱窝】扩容；科技树、水培、深网与军备正在按里程碑开发（模块 E ~ I）。
          </span>
        </div>

        <div class="flex-1 space-y-1 overflow-auto p-3 text-[12px]">
          <div v-if="colony.logs.length === 0" class="text-terminal-dim">
            终端待机中…… 所有操作与后端返回都会在这里留痕（不做任何阻断弹窗）。
          </div>
          <div
            v-for="entry in colony.logs"
            :key="entry.id"
            class="flex gap-2"
            :class="{
              'text-terminal-text': entry.kind === 'info',
              'text-terminal-warn': entry.kind === 'warn',
              'text-terminal-crit': entry.kind === 'crit',
            }"
          >
            <span class="text-terminal-dim">{{ entry.at }}</span>
            <span>{{ entry.text }}</span>
          </div>
        </div>
      </section>

      <div class="col-span-3 flex min-h-0 flex-col gap-2">
        <ProductionPanel class="min-h-0 flex-1" />
        <WarRoomPanel class="min-h-0 flex-1" />
      </div>
    </main>

    <RadioTicker />

    <Modal
      v-if="offlineOpen && offlineSummary"
      title="离线休整报表"
      confirm-text="继续唤醒"
      @close="offlineOpen = false"
    >
      <p>你离开了 {{ offlineSummary.duration }}，避难所的机器仍在运转：</p>
      <ul class="space-y-1">
        <li>猫薄荷：{{ offlineSummary.catnip.toFixed(1) }}</li>
        <li>机械废铁：{{ offlineSummary.scrap.toFixed(1) }}</li>
        <li v-if="offlineSummary.batches > 0">
          航空钛合金：{{ offlineSummary.alloys.toFixed(1) }}
          <span class="text-terminal-dim">（电炉 {{ offlineSummary.batches }} 炉次）</span>
        </li>
        <li>新出生猫口：{{ offlineSummary.cats }}</li>
        <li v-if="offlineSummary.unity > 0">文明凝聚力：+{{ offlineSummary.unity.toFixed(2) }}</li>
        <li v-if="offlineSummary.research">
          科研：{{ offlineSummary.research?.unlocked ? '已解锁' : '推进中' }}
          【{{ offlineSummary.research?.tech_name }}】
        </li>
      </ul>
      <p v-if="offlineSummary.starved" class="text-terminal-crit">
        离线期间发生过断粮：拾荒/科研/踩轮产出锁死，繁育进度倒退，农夫猫靠 30% 求生本能翻野草。
      </p>
      <p v-if="offlineSummary.capped" class="text-terminal-warn">
        仓储爆仓：{{ offlineSummary.overflowed.map((key) => RESOURCE_LABELS[key] ?? key).join('、') }} 溢出部分被丢弃。
      </p>
      <p v-if="offlineSummary.clockAnomaly" class="text-terminal-warn">
        检测到系统时间回拨：仅重置结算锚点，不做任何补偿。
      </p>
    </Modal>

    <Modal
      v-if="colony.narrative"
      title="通风管道深处的声音"
      confirm-text="把它抱进纸箱"
      @close="colony.dismissNarrative()"
    >
      <p class="leading-relaxed">{{ colony.narrative }}</p>
    </Modal>

    <div
      v-if="colony.lastError"
      class="panel fixed bottom-12 right-3 z-40 max-w-md border-terminal-crit/60 px-3 py-2 text-[12px] text-terminal-crit"
    >
      <Hammer class="mr-1 inline h-3.5 w-3.5" />
      {{ colony.lastError }}
    </div>
  </div>
</template>
