import { onMounted, onUnmounted } from 'vue'

import { useColonyStore } from '@/stores/colony'

/** 静默快照周期（与 AUTOSAVE_INTERVAL_MS 环境变量一致：15s） */
export const AUTOSAVE_INTERVAL_MS = 15_000

/**
 * 15 秒静默快照（代码结构稿 §3.1）：
 * 无 Loading、无打断；关页面最后 ≤15 秒的收益由后端按 `last_tick_time` 重算，既不丢失也不重复。
 */
export function useAutoSave() {
  const colony = useColonyStore()
  let timer: number | null = null
  let lastRun = 0

  async function run() {
    if (!colony.loaded) return
    lastRun = Date.now()
    await colony.snapshot()
  }

  function handleVisibility() {
    // 切回前台时若已超过一个周期则立刻补一次快照（不闪任何 UI）
    if (document.visibilityState === 'visible' && Date.now() - lastRun >= AUTOSAVE_INTERVAL_MS) {
      void run()
    }
  }

  onMounted(() => {
    timer = window.setInterval(run, AUTOSAVE_INTERVAL_MS)
    document.addEventListener('visibilitychange', handleVisibility)
  })

  onUnmounted(() => {
    if (timer !== null) window.clearInterval(timer)
    document.removeEventListener('visibilitychange', handleVisibility)
    timer = null
  })

  return { intervalMs: AUTOSAVE_INTERVAL_MS, runNow: run }
}
