<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Copy, Download, HardDriveDownload, RefreshCw, Upload } from 'lucide-vue-next'

import Badge from '@/components/common/Badge.vue'
import { useColonyStore } from '@/stores/colony'
import { useSaveStore } from '@/stores/save'
import { formatDuration } from '@/utils/format'

const colony = useColonyStore()
const save = useSaveStore()

const importText = ref('')
const importName = ref('')
const targetSlot = ref<number>(colony.slotId)
const copied = ref(false)

onMounted(() => save.refreshSlots())

const exportText = computed(() => save.exported?.base64_payload ?? '')
const exportSummary = computed(() => {
  const data = save.exported
  if (!data) return null
  return {
    slot: data.slot,
    rows: data.total_rows,
    size: `${(data.raw_bytes / 1024).toFixed(1)} KB`,
    checksum: `${data.checksum.slice(0, 12)}…`,
    version: data.save_version,
  }
})

async function copyPayload() {
  if (!exportText.value) return
  try {
    await navigator.clipboard.writeText(exportText.value)
    copied.value = true
    window.setTimeout(() => (copied.value = false), 1500)
    colony.log('存档文本已复制到剪贴板')
  } catch {
    colony.log('剪贴板不可用：请手动全选下方的 Base64 文本复制', 'warn')
  }
}

async function doImport() {
  if (!importText.value.trim()) {
    colony.log('请先粘贴 Base64 存档文本', 'warn')
    return
  }
  const result = await save.importSlot(targetSlot.value, importText.value, importName.value || undefined)
  if (result) {
    importText.value = ''
    importName.value = ''
  }
}
</script>

<template>
  <section class="panel flex h-full flex-col">
    <div class="panel-title">
      <HardDriveDownload class="h-3.5 w-3.5" />
      <span>存档槽位</span>
      <Badge :text="`存档版本 v${save.saveVersion}`" />
      <button class="btn ml-auto border-none px-1" :disabled="save.busy" @click="save.refreshSlots()">
        <RefreshCw class="h-3 w-3" />
      </button>
    </div>

    <div class="flex-1 space-y-3 overflow-auto p-3 text-[12px]">
      <div
        v-for="slot in save.slots"
        :key="slot.slot"
        class="space-y-1 rounded border p-2"
        :class="slot.slot === colony.slotId ? 'border-terminal-accent/50' : 'border-terminal-line/70'"
      >
        <div class="flex items-center gap-2">
          <span :class="slot.slot === colony.slotId ? 'text-terminal-accent' : 'text-terminal-text'">
            槽位 {{ slot.slot }} · {{ slot.name ?? '空槽位' }}
          </span>
          <Badge v-if="slot.slot === colony.slotId" text="当前" tone="accent" />
          <span class="ml-auto flex gap-1">
            <button
              v-if="slot.exists"
              class="btn px-2 py-0.5"
              :disabled="save.busy"
              @click="save.switchSlot(slot.slot)"
            >
              切到此档
            </button>
            <button
              v-if="slot.exists"
              class="btn px-2 py-0.5"
              :disabled="save.busy"
              @click="save.exportSlot(slot.slot)"
            >
              <Download class="mr-1 inline h-3 w-3" />导出
            </button>
          </span>
        </div>
        <div v-if="slot.exists" class="grid grid-cols-2 gap-x-3 text-[10px] text-terminal-dim">
          <span>时长 {{ formatDuration(slot.playtime_seconds ?? 0) }}</span>
          <span>猫口 {{ slot.cats_total ?? 0 }}</span>
          <span>活跃星球 #{{ slot.active_planet_id ?? 0 }}</span>
          <span>凝聚力 {{ (slot.unity ?? 0).toFixed(1) }}（政令 {{ slot.doctrines ?? 0 }}）</span>
          <span class="col-span-2">最后写入 {{ slot.updated_at ?? '—' }}</span>
        </div>
        <p v-else class="text-[10px] text-terminal-dim">空槽位：可直接导入一份 Base64 存档到这里。</p>
      </div>

      <div v-if="exportSummary" class="space-y-1 rounded border border-terminal-line/70 p-2">
        <div class="flex items-center gap-2">
          <span>导出结果：槽位 {{ exportSummary.slot }}</span>
          <Badge :text="`${exportSummary.rows} 行 / ${exportSummary.size}`" tone="accent" />
          <button class="btn ml-auto px-2 py-0.5" @click="copyPayload">
            <Copy class="mr-1 inline h-3 w-3" />{{ copied ? '已复制' : '复制' }}
          </button>
        </div>
        <p class="text-[10px] text-terminal-dim">
          SHA-256 {{ exportSummary.checksum }} · 存档版本 v{{ exportSummary.version }} · Gzip + Base64
        </p>
        <textarea
          class="h-20 w-full resize-none rounded border border-terminal-line/70 bg-transparent p-1 font-mono text-[10px] text-terminal-text"
          readonly
          :value="exportText"
        />
      </div>

      <div class="space-y-2 rounded border border-terminal-line/70 p-2">
        <div class="flex items-center gap-2">
          <Upload class="h-3.5 w-3.5 text-terminal-accent" />
          <span>导入存档</span>
          <select v-model.number="targetSlot" class="ml-auto rounded border border-terminal-line/70 bg-transparent px-1 text-[11px]">
            <option v-for="slot in save.slots" :key="slot.slot" :value="slot.slot">槽位 {{ slot.slot }}</option>
          </select>
        </div>
        <input
          v-model="importName"
          class="w-full rounded border border-terminal-line/70 bg-transparent px-1 text-[11px]"
          placeholder="可选：给该槽位改个名字"
          maxlength="32"
        >
        <textarea
          v-model="importText"
          class="h-20 w-full resize-none rounded border border-terminal-line/70 bg-transparent p-1 font-mono text-[10px] text-terminal-text"
          placeholder="把导出的 Base64 文本粘贴到这里"
        />
        <button class="btn btn-primary w-full py-1" :disabled="save.busy" @click="doImport">
          导入并整体替换槽位 {{ targetSlot }}
        </button>
        <p class="text-[10px] text-terminal-dim">
          导入前先比对 SHA-256 校验和与存档版本；校验失败一律拒绝，不做"尽力恢复"。
        </p>
      </div>
    </div>
  </section>
</template>
