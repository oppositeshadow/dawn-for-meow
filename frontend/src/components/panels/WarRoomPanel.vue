<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Crosshair, ShieldAlert, Truck, Wrench, PackageCheck } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import GaugeBar from '@/components/common/GaugeBar.vue'
import { useMilitaryStore } from '@/stores/military'
import { useColonyStore } from '@/stores/colony'
import type { VehicleView } from '@/types/game'

const military = useMilitaryStore()
const colonyStore = useColonyStore()
const selectedType = ref('light_car')
const selectedTarget = ref('WALMART')
const pickedUnits = ref<number[]>([])
const ambushUnits = ref<number[]>([])
const assaultStage = ref(1)

onMounted(() => {
  military.startPolling(5000)
})
onUnmounted(() => military.stopPolling())

const view = computed(() => military.hangar)
const types = computed(() => Object.entries(view.value?.vehicle_types ?? {}))
const targets = computed(() => Object.entries(view.value?.expedition_targets ?? {}))

const assembleHint = computed(() => military.assembleHint(selectedType.value))

// 车载模块（§9.10）：每辆车一个待装选择，模块目录来自 /vehicle/list
const modulePick = ref<Record<number, string>>({})
const modules = computed(() => view.value?.vehicle_modules ?? {})
const moduleIds = computed(() => Object.keys(modules.value))

function moduleName(moduleId: string): string {
  return (modules.value as Record<string, { name: string }>)[moduleId]?.name ?? moduleId
}

function statusTone(vehicle: VehicleView): 'accent' | 'warn' | 'dim' | 'crit' {
  if (vehicle.status === 'IDLE') return 'accent'
  if (vehicle.status === 'EXPEDITION') return 'dim'
  return 'warn'
}

function remaining(endsAt: number | null): string {
  if (!endsAt) return '—'
  const left = Math.max(0, endsAt - military.now)
  const minutes = Math.floor(left / 60)
  const seconds = left % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function targetDrops(targetId: string): string {
  const target = view.value?.expedition_targets[targetId]
  if (!target) return ''
  return Object.entries(target.drops)
    .map(([resource, amount]) => `${resource === 'scrap' ? '废铁' : resource === 'chips' ? '芯片' : resource === 'alloys' ? '合金' : '猫薄荷'} ${amount}`)
    .join(' + ')
}

function targetRequirement(targetId: string): string | null {
  const required = view.value?.expedition_targets[targetId]?.requires_unit_types ?? []
  if (!required.length) return null
  return required
    .map((type) => view.value?.vehicle_types[type]?.name ?? type)
    .join(' / ')
}

function togglePick(unitId: number) {
  pickedUnits.value = pickedUnits.value.includes(unitId)
    ? pickedUnits.value.filter((id) => id !== unitId)
    : [...pickedUnits.value, unitId]
}

function toggleAmbush(unitId: number) {
  ambushUnits.value = ambushUnits.value.includes(unitId)
    ? ambushUnits.value.filter((id) => id !== unitId)
    : [...ambushUnits.value, unitId]
}

const tacticalLabel: Record<string, string> = {
  OVERCLOCK: '过载超频',
  EMP: '电磁脉冲',
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <Crosshair class="h-3.5 w-3.5" />
      <span>战备机库</span>
      <Badge :text="`机位 ${view?.hangar_used ?? 0}/${view?.hangar_capacity ?? 0}`" tone="accent" />
      <Badge :text="`诱饵 ${view?.decoy_count ?? 0}`" />
      <Badge v-if="(view?.hospital_queue.length ?? 0) > 0" :text="`急救舱 ${view?.hospital_queue.length}`" tone="warn" />
    </div>

    <div class="flex-1 space-y-3 overflow-auto p-3 text-[12px]">
      <!-- 组装 -->
      <div class="flex items-center gap-2">
        <select v-model="selectedType" class="rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]">
          <option v-for="[key, spec] in types" :key="key" :value="key">
            {{ spec.name }}（{{ military.costText(spec.cost) }}｜乘员 {{ spec.crew }}）
          </option>
        </select>
        <button
          class="btn btn-primary px-2 py-0.5"
          :disabled="military.busy || assembleHint !== null"
          @click="military.assemble(selectedType)"
        >
          <Truck class="mr-1 inline h-3 w-3" />组装
        </button>
        <span v-if="assembleHint" class="text-[10px] text-terminal-dim">{{ assembleHint }}</span>
      </div>

      <!-- 载具列表 -->
      <div v-if="(view?.vehicles.length ?? 0) === 0" class="text-terminal-dim">
        机库空空如也：造一辆轻装猫车，就能开始废墟远征与满警戒度截杀。
      </div>
      <div v-for="vehicle in view?.vehicles ?? []" :key="vehicle.unit_id" class="space-y-1 rounded border border-terminal-line/70 px-2 py-1.5">
        <div class="flex items-center gap-2">
          <span>{{ vehicle.nickname ?? vehicle.unit_name }}</span>
          <span class="text-[10px] text-terminal-dim">{{ vehicle.unit_name }} · CP {{ vehicle.combat_power }}</span>
          <Badge
            :text="vehicle.status === 'IDLE' ? '待命' : vehicle.status === 'REPAIR' ? `维修 ${remaining(vehicle.repair_ends_at)}` : '远征中'"
            :tone="statusTone(vehicle)"
          />
          <span class="ml-auto flex gap-1">
            <button v-if="vehicle.status === 'REPAIR'" class="btn px-1.5 py-0" :disabled="military.busy" @click="military.repair(vehicle.unit_id)">
              <Wrench class="h-3 w-3" />
            </button>
            <button v-if="vehicle.status !== 'EXPEDITION'" class="btn px-1.5 py-0" :disabled="military.busy" @click="military.scrap(vehicle.unit_id)">
              <ShieldAlert class="h-3 w-3" />拆
            </button>
          </span>
        </div>
        <div class="grid grid-cols-3 gap-1">
          <GaugeBar :value="vehicle.shield" :max="vehicle.shield || 1" :height="4" />
          <GaugeBar :value="vehicle.armor" :max="vehicle.armor_max || 1" tone="warn" :height="4" />
          <GaugeBar :value="vehicle.hull" :max="vehicle.armor_max ? vehicle.hull : 1" tone="crit" :height="4" />
        </div>
        <div class="flex gap-3 text-[10px] text-terminal-dim">
          <span>护盾 {{ vehicle.shield.toFixed(0) }}</span>
          <span>装甲 {{ vehicle.armor.toFixed(0) }}/{{ vehicle.armor_max.toFixed(0) }}</span>
          <span>结构 {{ vehicle.hull.toFixed(0) }}</span>
          <span>乘员 {{ vehicle.crew_cats }}</span>
        </div>
        <div class="flex items-center gap-2 text-[10px]">
          <span class="text-terminal-dim">模块</span>
          <span v-if="vehicle.modules.length === 0" class="text-terminal-dim">（空槽）</span>
          <button
            v-for="moduleId in vehicle.modules"
            :key="moduleId"
            class="btn px-1.5 py-0"
            :disabled="military.busy || vehicle.status !== 'IDLE'"
            :title="moduleName(moduleId)"
            @click="military.unequipModule(vehicle.unit_id, moduleId)"
          >
            {{ moduleName(moduleId) }} ✕
          </button>
          <select
            v-model="modulePick[vehicle.unit_id]"
            class="ml-auto rounded border border-terminal-line bg-transparent px-1 py-0 text-[10px]"
            :disabled="military.busy || vehicle.status !== 'IDLE'"
          >
            <option :value="''">选模块…</option>
            <option v-for="candidate in moduleIds" :key="candidate" :value="candidate">
              {{ moduleName(candidate) }}
            </option>
          </select>
          <button
            class="btn px-1.5 py-0"
            :disabled="military.busy || vehicle.status !== 'IDLE' || !modulePick[vehicle.unit_id]"
            @click="military.equipModule(vehicle.unit_id, modulePick[vehicle.unit_id])"
          >
            装
          </button>
        </div>
      </div>

      <!-- 远征 -->
      <div class="space-y-1 border-t border-terminal-line pt-2">
        <div class="flex items-center gap-2">
          <select v-model="selectedTarget" class="rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]">
            <option v-for="[key, target] in targets" :key="key" :value="key">
              {{ target.name }}（{{ Math.round(target.duration_seconds / 60) }} 分钟｜{{ targetDrops(key) }}）
            </option>
          </select>
          <span v-if="targetRequirement(selectedTarget)" class="text-[10px] text-terminal-warn">
            需 {{ targetRequirement(selectedTarget) }}
          </span>
          <button
            class="btn ml-auto px-2 py-0.5"
            :disabled="military.busy || pickedUnits.length === 0"
            @click="military.dispatch(selectedTarget, pickedUnits); pickedUnits = []"
          >
            派遣（{{ pickedUnits.length }} 辆）
          </button>
        </div>
        <div class="flex flex-wrap gap-2">
          <label v-for="vehicle in military.idleVehicles" :key="vehicle.unit_id" class="flex items-center gap-1 text-[11px]">
            <input
              type="checkbox"
              class="accent-terminal-accent"
              :checked="pickedUnits.includes(vehicle.unit_id)"
              @change="togglePick(vehicle.unit_id)"
            />
            {{ vehicle.nickname ?? vehicle.unit_name }}
          </label>
        </div>
      </div>

      <!-- 在途远征 -->
      <div v-if="(view?.active_expeditions.length ?? 0) > 0" class="space-y-1 border-t border-terminal-line pt-2">
        <div class="text-[11px] text-terminal-dim">在途远征</div>
        <div v-for="entry in view?.active_expeditions ?? []" :key="entry.expedition_id" class="flex items-center gap-2">
          <span>{{ view?.expedition_targets[entry.target_id]?.name ?? entry.target_id }}</span>
          <Badge v-if="entry.collected" text="已收取" />
          <!-- 已返航是"待办事项"，要和"还在路上"在视觉上区分开（灰字容易被当成进度提示忽略） -->
          <Badge v-else-if="military.now >= entry.ends_at" text="已返航 · 待收取" tone="accent" />
          <span v-else class="text-[10px] text-terminal-dim">返航 {{ remaining(entry.ends_at) }}</span>
          <button
            class="btn ml-auto px-1.5 py-0"
            :disabled="military.busy || entry.collected || military.now < entry.ends_at"
            @click="military.collect(entry.expedition_id)"
          >
            <PackageCheck class="mr-1 inline h-3 w-3" />收取
          </button>
        </div>
      </div>

      <!-- 最近一场交战（可回看，不必去终端里翻） -->
      <div v-if="military.lastBattle" class="space-y-1 border-t border-terminal-line pt-2">
        <div class="flex items-center gap-2 text-[11px]">
          <span class="text-terminal-dim">最近一场交战</span>
          <span>{{ military.lastBattle.title }}</span>
          <Badge
            :text="military.lastBattle.won ? '胜' : '败'"
            :tone="military.lastBattle.won ? 'accent' : 'crit'"
          />
          <span class="ml-auto text-[10px] text-terminal-dim">共 {{ military.lastBattle.rounds }} 回合</span>
        </div>
        <div class="max-h-24 space-y-0.5 overflow-auto font-mono text-[10px] text-terminal-dim">
          <div v-for="(line, index) in military.lastBattle.log" :key="index">{{ line }}</div>
        </div>
      </div>

      <!-- 急救舱 -->
      <div v-if="(view?.hospital_queue.length ?? 0) > 0" class="space-y-1 border-t border-terminal-line pt-2">
        <div class="text-[11px] text-terminal-dim">急救舱（绝无死猫）</div>
        <div v-for="item in view?.hospital_queue ?? []" :key="item.unit_id" class="flex items-center gap-2 text-[11px]">
          <span>乘员 {{ item.cats }} 只休养中</span>
          <span class="ml-auto text-terminal-dim">
            {{ military.now >= item.ends_at ? '休养期满 · 下次结算归队' : `归队 ${remaining(item.ends_at)}` }}
          </span>
        </div>
      </div>

      <!-- 战术指令与破壁工程 -->
      <div class="space-y-1 border-t border-terminal-line pt-2">
        <div class="flex items-center gap-2 text-[11px]">
          <span class="text-terminal-dim">战术电力（蓄电池 {{ colonyStore.power.battery_kwh.toFixed(0) }} kWh）</span>
          <Badge
            v-if="view?.tactical_buff"
            :text="`${tacticalLabel[view.tactical_buff.command] ?? view.tactical_buff.command} 待生效`"
            tone="warn"
          />
        </div>
        <div class="flex gap-1">
          <button class="btn px-1.5 py-0" :disabled="military.busy" @click="military.tacticalAction('OVERCLOCK')">
            过载超频（20kWh）
          </button>
          <button class="btn px-1.5 py-0" :disabled="military.busy" @click="military.tacticalAction('EMP')">
            电磁脉冲（15kWh）
          </button>
          <button class="btn px-1.5 py-0" :disabled="military.busy" @click="military.tacticalAction('EJECT')">
            紧急弹射撤离
          </button>
        </div>

        <div class="flex items-center gap-2 text-[11px]">
          <span class="text-terminal-dim">
            欧米茄车队：{{ view?.convoy_ends_at ? `到港 ${remaining(view.convoy_ends_at)}` : '在采矿区装货' }}
          </span>
          <Badge v-if="view?.factory_frozen_until && view.factory_frozen_until > military.now" :text="`兵工厂停工 ${remaining(view.factory_frozen_until)}`" tone="warn" />
          <span class="ml-auto text-[10px] text-terminal-dim">
            威胁 Lv.{{ view?.threat_level ?? 1 }} · 舰队 {{ (view?.fleet_strength ?? 0).toFixed(0) }} · rage {{ (view?.rage ?? 0).toFixed(0) }}
          </span>
        </div>
        <div class="flex flex-wrap gap-2">
          <label v-for="vehicle in military.idleVehicles" :key="`ambush-${vehicle.unit_id}`" class="flex items-center gap-1 text-[11px]">
            <input
              type="checkbox"
              class="accent-terminal-accent"
              :checked="ambushUnits.includes(vehicle.unit_id)"
              @change="toggleAmbush(vehicle.unit_id)"
            />
            {{ vehicle.nickname ?? vehicle.unit_name }}
          </label>
          <button
            class="btn px-1.5 py-0"
            :disabled="military.busy || ambushUnits.length === 0 || !view?.convoy_ends_at"
            @click="military.ambush(ambushUnits); ambushUnits = []"
          >
            伏击车队
          </button>
        </div>

        <div class="flex items-center gap-2 text-[11px]">
          <span class="text-terminal-dim">巡航导弹 {{ view?.cruise_missiles ?? 0 }} 枚</span>
          <button class="btn px-1.5 py-0" :disabled="military.busy" @click="military.assembleMissile()">
            总装（合金 30 + 芯片 10 + 电池 2）
          </button>
          <button
            class="btn btn-primary px-1.5 py-0"
            :disabled="military.busy || !view?.cruise_missiles"
            @click="military.launchMissile()"
          >
            点火发射
          </button>
        </div>

        <!-- 欧米茄终局（模块 L） -->
        <div class="flex flex-wrap items-center gap-2 border-t border-terminal-line/60 pt-2 text-[11px]">
          <Badge v-if="view?.completed" text="🎬 已通关" tone="accent" />
          <span class="text-terminal-dim">
            掠夺突袭：{{ view?.raid_ends_at ? remaining(view.raid_ends_at) : '装填中' }}
          </span>
          <button
            class="btn px-1.5 py-0"
            :disabled="military.busy || ambushUnits.length === 0 || !view?.raid_ends_at"
            @click="military.interceptRaid(ambushUnits); ambushUnits = []"
          >
            拦截掠夺
          </button>
          <span class="text-terminal-dim">决战进度 {{ view?.final_stage_cleared ?? 0 }}/3</span>
          <select v-model.number="assaultStage" class="rounded border border-terminal-line bg-transparent px-1 py-0.5 text-[11px]">
            <option v-for="stage in 3" :key="stage" :value="stage">
              第 {{ stage }} 段（{{ ['星门突破战', '分区总督舰队', '戴森主脑突入'][stage - 1] }}）
            </option>
          </select>
          <button
            class="btn btn-primary px-1.5 py-0"
            :disabled="military.busy || ambushUnits.length === 0 || view?.completed"
            @click="military.finalAssault(assaultStage, ambushUnits)"
          >
            发动决战
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
