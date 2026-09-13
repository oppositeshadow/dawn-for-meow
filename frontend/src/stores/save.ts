import { defineStore } from 'pinia'
import { ref } from 'vue'

import { useColonyStore } from '@/stores/colony'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export interface SaveSlotMeta {
  slot: number
  exists: boolean
  name: string | null
  save_version: number | null
  active_planet_id?: number
  playtime_seconds?: number
  playtime_hours?: number
  cats_total?: number
  unity?: number
  doctrines?: number
  updated_at?: string | null
  checksum?: string | null
}

export interface SaveExportResult {
  slot: number
  base64_payload: string
  checksum: string
  save_version: number
  raw_bytes: number
  total_rows: number
}

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

/** 多槽位存档仓（模块 N4）：导出 Base64 / 导入替换 / 槽位切换。 */
export const useSaveStore = defineStore('save', () => {
  const colony = useColonyStore()
  const slots = ref<SaveSlotMeta[]>([])
  const saveVersion = ref(1)
  const exported = ref<SaveExportResult | null>(null)
  const busy = ref(false)

  async function refreshSlots() {
    try {
      const payload = await request<{ data: { slots: SaveSlotMeta[]; save_version: number } }>('/save/slots')
      slots.value = payload.data.slots
      saveVersion.value = payload.data.save_version
    } catch {
      /* 后端不可用时保留上一次视图 */
    }
  }

  async function exportSlot(slot: number) {
    busy.value = true
    try {
      const data = await request<SaveExportResult>(`/save/export?slot=${slot}`)
      exported.value = data
      colony.log(`导出存档 ${slot}：${data.total_rows} 行 / ${(data.raw_bytes / 1024).toFixed(1)} KB`)
      return data
    } catch (error) {
      colony.log(`导出失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
      return null
    } finally {
      busy.value = false
    }
  }

  async function importSlot(slot: number, base64Payload: string, slotName?: string) {
    busy.value = true
    try {
      const payload = await request<{ data: { total_rows: number; slot: number } }>('/save/import', {
        method: 'POST',
        body: JSON.stringify({
          slot,
          base64_payload: base64Payload.trim(),
          ...(slotName ? { slot_name: slotName } : {}),
        }),
      })
      colony.log(`导入存档到槽位 ${payload.data.slot}：共 ${payload.data.total_rows} 行，已整体替换`)
      await refreshSlots()
      if (payload.data.slot === colony.slotId) await colony.refresh({ silent: true })
      return payload.data
    } catch (error) {
      colony.log(`导入失败：${error instanceof Error ? error.message : String(error)}`, 'crit')
      return null
    } finally {
      busy.value = false
    }
  }

  async function switchSlot(slot: number) {
    colony.slotId = slot
    await colony.refresh({ silent: true })
    await refreshSlots()
    colony.log(`已切换到槽位 ${slot}`)
  }

  return { slots, saveVersion, exported, busy, refreshSlots, exportSlot, importSlot, switchSlot }
})
