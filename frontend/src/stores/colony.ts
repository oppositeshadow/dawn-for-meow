import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, ApiRequestError } from '@/api/client'
import type {
  ColonyStateData,
  HysteresisPolicy,
  LogEntry,
  OfflineReport,
  PowerState,
  PopulationState,
  Resources,
  SuspicionState,
} from '@/types/game'
import { DISPLAY_BALANCE, clamp } from '@/utils/balance'
import { clockText } from '@/utils/format'
import { useFacilitiesStore } from '@/stores/facilities'

const EMPTY_RESOURCES: Resources = {
  catnip: 0,
  scrap: 0,
  chips: 0,
  alloys: 0,
  battery: 0,
  lube: 0,
}

/**
 * 基地状态仓（代码结构稿 §3.2.1）。
 *
 * 双值设计：
 * * `serverResources` —— 后端权威值（每 15 秒快照或每次操作后刷新）；
 * * `resources`       —— 100ms 插值的**显示值**，只喂给渲染层；
 * 两者共用后端同一套公式参数（见 utils/balance.ts 的显示镜像说明）。
 */
export const useColonyStore = defineStore('colony', () => {
  const facilitiesStore = useFacilitiesStore()

  const slotId = ref(1)
  const planetId = ref(0)
  const loaded = ref(false)
  const busy = ref(false)
  const lastError = ref<string | null>(null)

  const serverResources = ref<Resources>({ ...EMPTY_RESOURCES })
  const resources = ref<Resources>({ ...EMPTY_RESOURCES })
  const caps = ref<Record<string, number>>({})
  const power = ref<PowerState>({
    gen_kw: 0,
    load_kw: 0,
    net_kw: 0,
    battery_kwh: 0,
    battery_kwh_max: 200,
    blackout: false,
  })
  const population = ref<PopulationState>({
    total: 0,
    max_cap: 0,
    unassigned: 0,
    birth_progress: 0,
  })
  const workstations = ref<Record<string, number>>({})
  const workstationLimits = ref<Record<string, number>>({})
  const facilities = ref<Record<string, number>>({})
  const suspicion = ref<SuspicionState>({ current: 0, max: 100 })
  const policy = ref<HysteresisPolicy | null>(null)
  const offlineReport = ref<OfflineReport | null>(null)
  const lastTickTime = ref(0)
  const lastSyncAt = ref(0)
  const logs = ref<LogEntry[]>([])
  const narrative = ref<string | null>(null)
  const manualClicksLeft = ref<number | null>(null)
  const localBirthProgress = ref(0)
  const localSuspicion = ref(0)
  let logSeq = 0

  function log(text: string, kind: LogEntry['kind'] = 'info') {
    logSeq += 1
    logs.value = [{ id: logSeq, at: clockText(), text, kind }, ...logs.value].slice(0, 80)
  }

  const catnipRate = computed(() => {
    const farmers = workstations.value.farmer ?? 0
    return (
      farmers * facilitiesStore.jobRate('farmer') -
      population.value.total * DISPLAY_BALANCE.catnipConsumePerCatPerSec
    )
  })

  const scrapRate = computed(
    () => (workstations.value.scavenger ?? 0) * facilitiesStore.jobRate('scavenger'),
  )

  const isStarving = computed(
    () => resources.value.catnip <= 0 && catnipRate.value < 0 && population.value.total > 0,
  )

  const coldStartPhase = computed(() => {
    if ((facilities.value.housing_box ?? 0) === 0) return 'GATHER' as const
    if ((facilities.value.farm_plot ?? 0) === 0) return 'FIRST_FARM' as const
    if ((workstations.value.farmer ?? 0) === 0) return 'ASSIGN_FARMER' as const
    if ((facilities.value.scavenge_station ?? 0) === 0) return 'AUTOMATE_SCRAP' as const
    // 有闲置猫口就先上岗（手上有猫比扩容更急）
    if ((workstations.value.scavenger ?? 0) === 0) return 'ASSIGN_SCAVENGER' as const
    // 农夫与拾荒猫都上工 ⇒ 冷启动心流闭环完成，后续交给长线经营
    return 'DONE' as const
  })

  function applyState(data: ColonyStateData) {
    slotId.value = data.slot_id
    planetId.value = data.planet_id
    serverResources.value = {
      catnip: data.resources.catnip,
      scrap: data.resources.scrap,
      chips: data.resources.chips,
      alloys: data.resources.alloys,
      battery: data.resources.battery,
      lube: data.resources.lube,
    }
    resources.value = { ...serverResources.value }
    caps.value = data.resources.caps ?? {}
    power.value = data.power
    population.value = data.population
    workstations.value = data.workstations
    workstationLimits.value = data.workstation_limits
    facilities.value = data.facilities
    suspicion.value = data.suspicion
    offlineReport.value = data.offline_report
    lastTickTime.value = data.last_tick_time
    lastSyncAt.value = Date.now()
    localBirthProgress.value = data.population.birth_progress
    localSuspicion.value = data.suspicion.current
    loaded.value = true
  }

  /** 100ms 高频时钟：只做显示插值，绝不回写权威值 */
  function tick(dtSeconds: number) {
    if (!loaded.value) return
    const next = { ...resources.value }
    next.catnip = clamp(next.catnip + catnipRate.value * dtSeconds, 0, caps.value.catnip ?? 200)
    next.scrap = clamp(next.scrap + scrapRate.value * dtSeconds, 0, caps.value.scrap ?? 200)
    resources.value = next

    const { total, max_cap } = population.value
    if (total > 0 && max_cap > 0 && total < max_cap) {
      const r = DISPLAY_BALANCE.breedingRate
      localBirthProgress.value += r * total * (1 - total / max_cap) * dtSeconds
    }

    const activeFacilities = Object.values(facilities.value).filter((level) => level > 0).length
    const rate =
      DISPLAY_BALANCE.suspicionBaseNoise +
      DISPLAY_BALANCE.suspicionPerActiveFacility * activeFacilities -
      DISPLAY_BALANCE.suspicionIdleDecay
    localSuspicion.value = clamp(
      localSuspicion.value + rate * dtSeconds,
      0,
      suspicion.value.max,
    )
  }

  async function refresh(options: { silent?: boolean; report?: boolean } = {}) {
    try {
      const envelope = await api.getState(slotId.value)
      applyState(envelope.data)
      if (!options.silent) {
        const report = envelope.data.offline_report
        if (report && report.elapsed_seconds > 0) {
          log(
            `读档完成：离线 ${Math.round(report.elapsed_seconds)} 秒，猫薄荷 ${report.gained_catnip.toFixed(1)}、废铁 ${report.gained_scrap.toFixed(1)}、新猫 ${report.gained_cats}`,
          )
          for (const note of report.notes) log(note, 'warn')
        }
      }
      lastError.value = null
    } catch (error) {
      handleError(error, '读档失败')
    }
  }

  function handleError(error: unknown, prefix: string) {
    if (error instanceof ApiRequestError) {
      const text = error.detail ? `${prefix}：${error.message}（${error.detail}）` : `${prefix}：${error.message}`
      lastError.value = text
      log(text, 'crit')
    } else {
      lastError.value = String(error)
      log(`${prefix}：${String(error)}`, 'crit')
    }
    window.setTimeout(() => {
      lastError.value = null
    }, 5000)
  }

  async function scavenge() {
    busy.value = true
    try {
      const envelope = await api.scavenge(slotId.value)
      const data = envelope.data
      resources.value = { ...resources.value, scrap: data.scrap }
      serverResources.value = { ...serverResources.value, scrap: data.scrap }
      manualClicksLeft.value = data.clicks_left
      log(`翻检地表建筑废墟：+1 机械废铁（累计 ${data.manual_scavenge_clicks} 次）`)
      if (data.hint) log(data.hint, 'warn')
      await refresh({ silent: true })
    } catch (error) {
      handleError(error, '翻找失败')
    } finally {
      busy.value = false
    }
  }

  async function build(facilityId: string, count = 1) {
    busy.value = true
    try {
      const envelope = await api.build({ slot: slotId.value, facility_id: facilityId, count })
      const data = envelope.data
      log(`${data.facility_id} 建造完成 → Lv.${data.level}（花费 ${JSON.stringify(data.cost_paid)}）`)
      if (data.narrative) {
        narrative.value = data.narrative
        log('一只折耳流浪猫入驻了第一个纸箱窝！', 'info')
      }
      await refresh({ silent: true })
    } catch (error) {
      handleError(error, '建造失败')
    } finally {
      busy.value = false
    }
  }

  async function dispatch(role: string, delta: number, nextPolicy?: HysteresisPolicy) {
    busy.value = true
    try {
      const envelope = await api.dispatch({
        slot: slotId.value,
        role,
        delta,
        ...(nextPolicy ? { policy: nextPolicy } : {}),
      })
      const data = envelope.data
      if (nextPolicy) policy.value = nextPolicy
      log(`工位调度：${role} ${delta > 0 ? '+' : ''}${delta} → ${data.count} 只（空闲 ${data.unassigned}）`)
      await refresh({ silent: true })
    } catch (error) {
      handleError(error, '调度失败')
    } finally {
      busy.value = false
    }
  }

  /** 15 秒静默快照：提交前端预测值（后端重算为准，偏差 > 0.5% 会被记警告） */
  async function snapshot() {
    try {
      const result = await api.snapshot({
        slot: slotId.value,
        planet_id: planetId.value,
        client_time: Date.now() / 1000,
        resources: { ...resources.value },
        population: {
          total: population.value.total,
          birth_progress: Number(localBirthProgress.value.toFixed(4)),
        },
        workstations: { ...workstations.value },
      })
      lastSyncAt.value = Date.now()
      return result.message
    } catch (error) {
      handleError(error, '快照失败')
      return null
    }
  }

  function dismissNarrative() {
    narrative.value = null
  }

  function clearLogs() {
    logs.value = []
  }

  return {
    slotId,
    planetId,
    loaded,
    busy,
    lastError,
    resources,
    serverResources,
    caps,
    power,
    population,
    workstations,
    workstationLimits,
    facilities,
    suspicion,
    policy,
    offlineReport,
    lastTickTime,
    lastSyncAt,
    logs,
    narrative,
    manualClicksLeft,
    localBirthProgress,
    localSuspicion,
    catnipRate,
    scrapRate,
    isStarving,
    coldStartPhase,
    applyState,
    tick,
    refresh,
    scavenge,
    build,
    dispatch,
    snapshot,
    dismissNarrative,
    clearLogs,
    log,
  }
})
