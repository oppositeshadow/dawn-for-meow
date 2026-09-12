import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api/client'
import type { FacilityDefinition, JobDefinition, Resources } from '@/types/game'
import { facilityCost } from '@/utils/balance'

/**
 * 设施与工种「定义」仓：定义来自后端 `static/*.json`（定义进 JSON 原则），
 * 等级与工位的实时状态在 colony 仓；这里只管定义、造价曲线与可负担性。
 */
export const useFacilitiesStore = defineStore('facilities', () => {
  const facilities = ref<FacilityDefinition[]>([])
  const jobs = ref<JobDefinition[]>([])
  const loaded = ref(false)

  async function loadDefinitions() {
    if (loaded.value) return
    const [facilityList, jobList] = await Promise.all([
      api.loadFacilityDefinitions(),
      api.loadJobDefinitions(),
    ])
    facilities.value = facilityList as FacilityDefinition[]
    jobs.value = jobList as JobDefinition[]
    loaded.value = true
  }

  const facilityMap = computed(() =>
    Object.fromEntries(facilities.value.map((item) => [item.facility_id, item])),
  )

  /** 母星可调度工种（载具乘员猫与星际职业单独处理） */
  const planetJobs = computed(() =>
    jobs.value.filter((job) => job.era === 'PLANET' && job.job_id !== 'crew'),
  )

  function nextCost(facilityId: string, currentLevel: number, count = 1): Record<string, number> {
    const definition = facilityMap.value[facilityId]
    if (!definition) return {}
    const total: Record<string, number> = {}
    for (let offset = 0; offset < count; offset += 1) {
      const cost = facilityCost(definition.cost, definition.growth, currentLevel + offset + 1)
      for (const [resource, amount] of Object.entries(cost)) {
        total[resource] = (total[resource] ?? 0) + amount
      }
    }
    return total
  }

  function affordable(cost: Record<string, number>, resources: Resources): boolean {
    return Object.entries(cost).every(
      ([resource, amount]) => (resources[resource as keyof Resources] ?? 0) >= amount,
    )
  }

  function jobName(jobId: string): string {
    return jobs.value.find((job) => job.job_id === jobId)?.name ?? jobId
  }

  function jobRate(jobId: string): number {
    return jobs.value.find((job) => job.job_id === jobId)?.output.rate_per_second ?? 0
  }

  return {
    facilities,
    jobs,
    loaded,
    facilityMap,
    planetJobs,
    loadDefinitions,
    nextCost,
    affordable,
    jobName,
    jobRate,
  }
})
