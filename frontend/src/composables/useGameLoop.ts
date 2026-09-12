import { onMounted, onUnmounted } from 'vue'

import { useColonyStore } from '@/stores/colony'

/** 高频插值周期（与 GAME_TICK_MS 环境变量一致：100ms） */
export const GAME_TICK_MS = 100

/**
 * 100ms 丝滑主循环（代码结构稿 §3.1）：
 * 只驱动**显示层**插值与动画，绝不做结算；切 Tab 时由浏览器节流，回来照样由后端补算。
 */
export function useGameLoop() {
  const colony = useColonyStore()
  let timer: number | null = null
  let lastFrame = 0

  function frame() {
    const now = performance.now()
    const deltaSeconds = Math.min((now - lastFrame) / 1000, 1)
    lastFrame = now
    colony.tick(deltaSeconds)
  }

  onMounted(() => {
    lastFrame = performance.now()
    timer = window.setInterval(frame, GAME_TICK_MS)
  })

  onUnmounted(() => {
    if (timer !== null) window.clearInterval(timer)
    timer = null
  })

  return { intervalMs: GAME_TICK_MS }
}
