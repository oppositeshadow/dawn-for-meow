import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { useColonyStore } from '@/stores/colony'
import { useFacilitiesStore } from '@/stores/facilities'
import type { HangarView, LootResult, VehicleView } from '@/types/game'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new Error(`${payload?.message ?? response.status}${payload?.detail ? `：${payload.detail}` : ''}`)
  }
  return payload as T
}

/** 战备机库仓（模块 G）：载具逐辆血条、组装/维修/拆解、异步远征与急救舱。 */
export const useMilitaryStore = defineStore('military', () => {
  const colony = useColonyStore()
  const facilitiesStore = useFacilitiesStore()
  const hangar = ref<HangarView | null>(null)
  const busy = ref(false)
  const now = ref(Math.floor(Date.now() / 1000))
  let pollTimer: number | null = null
  let clockTimer: number | null = null

  const idleVehicles = computed(() => (hangar.value?.vehicles ?? []).filter((v) => v.status === 'IDLE'))

  function costText(cost: Record<string, number>): string {
    return Object.entries(cost)
      .map(([resource, amount]) => `${amount} ${resource === 'scrap' ? '废铁' : resource === 'chips' ? '芯片' : resource === 'alloys' ? '合金' : resource}`)
      .join(' + ')
  }

  function canAfford(cost: Record<string, number>): boolean {
    return facilitiesStore.affordable(cost, colony.resources)
  }

  function assembleHint(unitType: string): string | null {
    const view = hangar.value
    if (!view) return '机库读取中…'
    const spec = view.vehicle_types[unitType]
    if (!spec) return '未知车型'
    if (view.hangar_used + spec.hangar_slots > view.hangar_capacity) return '机位不足'
    if (!canAfford(spec.cost)) return '材料不足'
    if (colony.population.unassigned < spec.crew) return `需要 ${spec.crew} 只空闲猫`
    return null
  }

  async function refresh() {
    try {
      const payload = await request<{ data: HangarView }>(
        `/vehicle/list?slot=${colony.slotId}&planet_id=${colony.planetId}`,
      )
      hangar.value = payload.data
    } catch {
      /* 后端不可用时保留上一次视图 */
    }
  }

  async function run<T>(label: string, action: () => Promise<T>, after?: (result: T) => void) {
    busy.value = true
    try {
      const result = await action()
      if (after) after(result)
      await refresh()
      await colony.refresh({ silent: true })
    } catch (error) {
      colony.log(`${label}失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
    } finally {
      busy.value = false
    }
  }

  function assemble(unitType: string, nickname?: string) {
    return run(
      '组装载具',
      () =>
        request<{ data: { vehicle: VehicleView; crew_locked: number } }>('/vehicle/assemble', {
          method: 'POST',
          body: JSON.stringify({
            slot: colony.slotId,
            planet_id: colony.planetId,
            unit_type: unitType,
            ...(nickname ? { nickname } : {}),
          }),
        }),
      (result) =>
        colony.log(
          `组装完成：${result.data.vehicle.nickname ?? result.data.vehicle.unit_name}（锁 ${result.data.crew_locked} 只乘员猫）`,
        ),
    )
  }

  function repair(unitId: number) {
    return run(
      '维修',
      () =>
        request<{ data: { cost_paid: Record<string, number>; seconds: number } }>('/vehicle/repair', {
          method: 'POST',
          body: JSON.stringify({ slot: colony.slotId, planet_id: colony.planetId, unit_id: unitId }),
        }),
      (result) => colony.log(`维修中：扣 ${costText(result.data.cost_paid)}，${result.data.seconds} 秒后回库`),
    )
  }

  function scrap(unitId: number) {
    return run(
      '拆解',
      () =>
        request<{ data: { refund: Record<string, number>; crew_released: number } }>('/vehicle/modify', {
          method: 'POST',
          body: JSON.stringify({
            slot: colony.slotId,
            planet_id: colony.planetId,
            unit_id: unitId,
            action: 'SCRAP',
          }),
        }),
      (result) => colony.log(`拆解回收 ${costText(result.data.refund)}，${result.data.crew_released} 只乘员猫归队`),
    )
  }

  /** 装配模块（《数值平衡表》§9.10）：科技门槛 / 槽位 / 材料由后端三段校验 */
  function equipModule(unitId: number, moduleId: string) {
    return run(
      `装配 ${moduleId}`,
      () =>
        request<{
          data: { modules: string[]; slots_used: number; slots_total: number; cost_paid: Record<string, number> }
        }>('/vehicle/modify', {
          method: 'POST',
          body: JSON.stringify({
            slot: colony.slotId,
            planet_id: colony.planetId,
            unit_id: unitId,
            action: 'EQUIP',
            module_id: moduleId,
          }),
        }),
      (result) =>
        colony.log(
          `装配完成：占用 ${result.data.slots_used}/${result.data.slots_total} 槽，扣 ${costText(result.data.cost_paid)}`,
        ),
    )
  }

  /** 拆卸模块：返还 50% 材料（鼓励试配装，§9.10） */
  function unequipModule(unitId: number, moduleId: string) {
    return run(
      `拆卸 ${moduleId}`,
      () =>
        request<{ data: { modules: string[]; refund: Record<string, number> } }>('/vehicle/modify', {
          method: 'POST',
          body: JSON.stringify({
            slot: colony.slotId,
            planet_id: colony.planetId,
            unit_id: unitId,
            action: 'UNEQUIP',
            module_id: moduleId,
          }),
        }),
      (result) => colony.log(`拆下 ${moduleId}，返还 ${costText(result.data.refund)}`),
    )
  }

  function dispatch(targetId: string, unitIds: number[]) {
    return run(
      '派遣远征',
      () =>
        request<{ data: { expedition_id: string; ends_at: number; duration_seconds: number } }>(
          '/military/dispatch',
          {
            method: 'POST',
            body: JSON.stringify({
              slot: colony.slotId,
              planet_id: colony.planetId,
              target_id: targetId,
              unit_ids: unitIds,
            }),
          },
        ),
      (result) =>
        colony.log(
          `远征出发：${hangar.value?.expedition_targets[targetId]?.name ?? targetId}，` +
            `${Math.round(result.data.duration_seconds / 60)} 分钟后可收取`,
        ),
    )
  }

  function collect(expeditionId: string) {
    return run(
      '收取战利品',
      () =>
        request<{ data: LootResult }>('/military/collect', {
          method: 'POST',
          body: JSON.stringify({
            slot: colony.slotId,
            planet_id: colony.planetId,
            expedition_id: expeditionId,
          }),
        }),
      (result) => {
        for (const line of result.data.report) colony.log(line, 'info')
      },
    )
  }

  const tacticalAction = (command: 'OVERCLOCK' | 'EMP' | 'EJECT') =>
    run(
      '战术指令',
      () => request<{ data: Record<string, any> }>('/military/tactical-action', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, planet_id: colony.planetId, command }),
      }),
      (result) => {
        const data = result.data
        colony.log(
          data.command === 'EJECT'
            ? `紧急弹射撤离：${data.recalled} 支编队返航，回收残骸 ${costText(data.refund)}`
            : `战术指令 ${data.command}：耗电 ${data.cost_kwh} kWh（下场交火生效）`,
        )
      },
    )

  const ambush = (unitIds: number[]) =>
    run(
      '伏击车队',
      () => request<{ data: Record<string, any> }>('/military/ambush-convoy', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, planet_id: colony.planetId, unit_ids: unitIds }),
      }),
      (result) => {
        const data = result.data
        for (const line of data.events ?? []) colony.log(String(line), data.won ? 'info' : 'warn')
      },
    )

  const assembleMissile = () =>
    run(
      '总装巡航导弹',
      () => request<{ data: Record<string, any> }>('/military/assemble-missile', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, planet_id: colony.planetId }),
      }),
      (result) => colony.log(`巡航导弹总装完成（库存 ${result.data.cruise_missiles} 枚，花费 ${costText(result.data.cost_paid)}）`),
    )

  const launchMissile = () =>
    run(
      '发射巡航导弹',
      () => request<{ data: Record<string, any> }>('/military/launch-missile', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, planet_id: colony.planetId }),
      }),
      (result) => {
        for (const line of result.data.events ?? []) colony.log(String(line), 'warn')
      },
    )

  const interceptRaid = (unitIds: number[]) =>
    run(
      '拦截掠夺舰队',
      () => request<{ data: Record<string, any> }>('/military/intercept-raid', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, planet_id: colony.planetId, unit_ids: unitIds }),
      }),
      (result) => {
        for (const line of result.data.events ?? []) colony.log(String(line), result.data.won ? 'info' : 'crit')
      },
    )

  const finalAssault = (stage: number, unitIds: number[]) =>
    run(
      '终局决战',
      () => request<{ data: Record<string, any> }>('/military/final-assault', {
        method: 'POST',
        body: JSON.stringify({ slot: colony.slotId, planet_id: colony.planetId, stage, unit_ids: unitIds }),
      }),
      (result) => {
        for (const line of result.data.events ?? []) colony.log(String(line), result.data.won ? 'warn' : 'crit')
        for (const line of result.data.staff_roll ?? []) colony.log(String(line), 'info')
      },
    )

  function startPolling(intervalMs = 5000) {
    if (pollTimer !== null) return
    now.value = Math.floor(Date.now() / 1000)
    void refresh()
    pollTimer = window.setInterval(refresh, intervalMs)
    clockTimer = window.setInterval(() => {
      now.value = Math.floor(Date.now() / 1000)
    }, 1000)
  }

  function stopPolling() {
    if (pollTimer !== null) window.clearInterval(pollTimer)
    if (clockTimer !== null) window.clearInterval(clockTimer)
    pollTimer = null
    clockTimer = null
  }

  return {
    hangar,
    busy,
    now,
    idleVehicles,
    costText,
    assembleHint,
    canAfford,
    refresh,
    assemble,
    repair,
    scrap,
    equipModule,
    unequipModule,
    dispatch,
    collect,
    tacticalAction,
    ambush,
    assembleMissile,
    launchMissile,
    interceptRaid,
    finalAssault,
    startPolling,
    stopPolling,
  }
})
