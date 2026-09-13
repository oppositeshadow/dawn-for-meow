<script setup lang="ts">
import { computed, ref } from 'vue'
import { Minus, Plus } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import GaugeBar from '@/components/common/GaugeBar.vue'
import { useColonyStore } from '@/stores/colony'
import { useFacilitiesStore } from '@/stores/facilities'

const colony = useColonyStore()
const facilitiesStore = useFacilitiesStore()

const policyEnabled = ref(true)
const policyUpper = ref(80)
const policyLower = ref(20)
const policyShift = ref(2)

// 面板上如实标出"科技到底给猫薄荷加了多少"（模块 E5），避免玩家以为有加成其实没接线
const techBonusPercent = computed(() =>
  Math.round((colony.techEffects.catnip_efficiency ?? 0) * 100),
)

const rows = computed(() =>
  facilitiesStore.planetJobs.map((job) => ({
    jobId: job.job_id,
    name: job.name,
    rate: job.output.rate_per_second,
    resource: job.output.resource,
    count: colony.workstations[job.job_id] ?? 0,
    limit: colony.workstationLimits[job.job_id] ?? 0,
  })),
)

function canAdd(limit: number, count: number): boolean {
  return count < limit && colony.population.unassigned > 0 && !colony.busy
}

function applyPolicy() {
  void colony.dispatch(rows.value[0]?.jobId ?? 'farmer', 0, {
    enabled: policyEnabled.value,
    upper: policyUpper.value / 100,
    lower: policyLower.value / 100,
    shift: policyShift.value,
  })
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <span>工位调度</span>
      <Badge
        v-if="techBonusPercent > 0"
        :text="`猫薄荷 +${techBonusPercent}%（科技）`"
        tone="accent"
      />
      <Badge :text="`空闲 ${colony.population.unassigned}`" tone="accent" />
    </div>

    <div class="flex-1 space-y-3 overflow-auto p-3">
      <div v-if="colony.population.total === 0" class="text-[12px] text-terminal-dim">
        还没有猫猫入驻：先造一座【瓦楞纸箱窝】，通风管道里的折耳猫会闻着纸箱味钻进来。
      </div>

      <div v-for="row in rows" :key="row.jobId" class="space-y-1">
        <div class="flex items-center gap-2">
          <span class="w-24 truncate text-[12px]">{{ row.name }}</span>
          <span class="tabular-nums text-[12px]">{{ row.count }}<span class="text-terminal-dim">/{{ row.limit }}</span></span>
          <span class="text-[10px] text-terminal-dim">
            {{ row.rate.toFixed(1) }}/s
          </span>
          <div class="ml-auto flex items-center gap-1">
            <button class="btn px-1.5 py-0.5" :disabled="colony.busy || row.count === 0" @click="colony.dispatch(row.jobId, -1)">
              <Minus class="h-3 w-3" />
            </button>
            <button class="btn px-1.5 py-0.5" :disabled="!canAdd(row.limit, row.count)" @click="colony.dispatch(row.jobId, 1)">
              <Plus class="h-3 w-3" />
            </button>
          </div>
        </div>
        <GaugeBar :value="row.count" :max="Math.max(row.limit, 1)" :height="4" />
      </div>

      <div class="space-y-1 border-t border-terminal-line pt-3">
        <div class="flex items-center gap-2">
          <span class="stat-label">繁育进度</span>
          <span class="tabular-nums">{{ Math.floor((colony.localBirthProgress % 1) * 100) }}%</span>
          <Badge v-if="colony.population.total >= colony.population.max_cap && colony.population.max_cap > 0" text="已满" tone="warn" />
        </div>
        <GaugeBar :value="colony.localBirthProgress % 1" :max="1" :height="4" />
      </div>

      <div class="space-y-2 border-t border-terminal-line pt-3">
        <div class="flex items-center gap-2">
          <input id="hysteresis" v-model="policyEnabled" type="checkbox" class="accent-terminal-accent" />
          <label for="hysteresis" class="stat-label">迟滞换班（防高频震荡）</label>
        </div>
        <div class="flex items-center gap-2 text-[11px]">
          <span class="stat-label">转出线</span>
          <input v-model.number="policyUpper" type="number" min="50" max="100" class="w-14 rounded border border-terminal-line bg-transparent px-1 py-0.5" />
          <span class="stat-label">回防线</span>
          <input v-model.number="policyLower" type="number" min="0" max="50" class="w-14 rounded border border-terminal-line bg-transparent px-1 py-0.5" />
          <span class="stat-label">批量</span>
          <input v-model.number="policyShift" type="number" min="1" max="10" class="w-12 rounded border border-terminal-line bg-transparent px-1 py-0.5" />
          <button class="btn ml-auto" :disabled="colony.busy" @click="applyPolicy">应用</button>
        </div>
        <p v-if="colony.policy" class="text-[10px] text-terminal-dim">
          已存策略：{{ colony.policy.enabled ? '开启' : '关闭' }} · 上 {{ Math.round(colony.policy.upper * 100) }}% · 下 {{ Math.round(colony.policy.lower * 100) }}% · 批量 {{ colony.policy.shift }}
        </p>
      </div>
    </div>
  </section>
</template>
