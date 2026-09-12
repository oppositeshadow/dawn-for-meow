export function formatAmount(value: number, digits = 1): string {
  if (!Number.isFinite(value)) return '0'
  if (Math.abs(value) >= 1000) return value.toFixed(0)
  return value.toFixed(digits)
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)} 秒`
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} 分钟`
  return `${(seconds / 3600).toFixed(2)} 小时`
}

export function clockText(at = new Date()): string {
  return at.toLocaleTimeString('zh-CN', { hour12: false })
}

export const RESOURCE_LABELS: Record<string, string> = {
  catnip: '猫薄荷',
  scrap: '废铁',
  chips: '芯片',
  alloys: '钛合金',
  battery: '电池',
  lube: '润滑脂',
}
