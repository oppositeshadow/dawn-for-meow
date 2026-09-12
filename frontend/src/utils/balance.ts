/**
 * 前端「显示用」数值镜像（**绝不是数值真值来源**）。
 *
 * 权威口径：`数值平衡表.md` → `backend/app/core/balance.py`；前端只用这些常量做 100ms 插值**显示**，
 * 任何产出、结算、扣费一律以后端重算结果为准（数据库设计定稿 §8 对账协议）。
 * 改数值时必须同时改：数值平衡表 → backend/app/core/balance.py → 本文件。
 */
export const DISPLAY_BALANCE = {
  catnipConsumePerCatPerSec: 0.05,
  breedingRate: 0.01,
  suspicionBaseNoise: 0.005,
  suspicionPerActiveFacility: 0.002,
  suspicionIdleDecay: 0.001,
  batteryChargePerKwSec: 0.1,
} as const

/** 设施造价：第 n 座 = 基础造价 × 递增系数^(n-1)，向上取整（与后端一致） */
export function facilityCost(
  cost: Record<string, number>,
  growth: number | null,
  nextLevel: number,
): Record<string, number> {
  const factor = growth === null ? 1 : growth ** (nextLevel - 1)
  const result: Record<string, number> = {}
  for (const [resource, amount] of Object.entries(cost)) {
    result[resource] = Math.ceil(amount * factor)
  }
  return result
}

export function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value))
}
