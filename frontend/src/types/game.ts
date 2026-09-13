/** 与后端 API 契约一一对应的强类型定义（代码结构稿 §4）。 */

export interface ApiEnvelope<T> {
  code: number
  data: T
}

export interface ApiError {
  code: number
  message: string
  detail?: string
}

export type ResourceKey = 'catnip' | 'scrap' | 'chips' | 'alloys' | 'battery' | 'lube'

export interface Resources {
  catnip: number
  scrap: number
  chips: number
  alloys: number
  battery: number
  lube: number
}

export interface PowerState {
  gen_kw: number
  load_kw: number
  net_kw: number
  battery_kwh: number
  battery_kwh_max: number
  blackout: boolean
}

export interface PopulationState {
  total: number
  max_cap: number
  unassigned: number
  birth_progress: number
}

export interface SuspicionState {
  current: number
  max: number
}

export interface SecurityState {
  decoy_count: number
  cooldown_until: number | null
  cooldown_left_seconds: number
  go_dark: boolean
  policy: Record<string, boolean>
}

export interface LaunchSiloStage {
  stage: number
  name: string
  cost: Record<string, number>
  done: boolean
  blocked: boolean
}

export interface LaunchSiloBlock {
  level: number
  max_level: number
  stages: LaunchSiloStage[]
  next_stage: LaunchSiloStage | null
  can_advance: boolean
  fortress_down: boolean
  launched: boolean
}

export interface OfflineReport {
  elapsed_seconds: number
  applied_seconds: number
  gained_catnip: number
  gained_scrap: number
  gained_cats: number
  gained_research: number
  is_starved: boolean
  starve_duration_seconds: number
  is_capped: boolean
  overflowed_resources: string[]
  suspicion_delta: number
  charged_kwh: number
  /** 熔炼（《数值平衡表》§3.5）：本段离线炼了几炉、产出多少合金 */
  smelted_batches: number
  gained_alloys: number
  clock_anomaly: boolean
  birth_progress: number
  notes: string[]
}

export interface ColonyStateData {
  slot_id: number
  planet_id: number
  last_tick_time: number
  saved_at: number
  resources: Resources & { caps: Record<string, number> }
  power: PowerState
  population: PopulationState
  workstations: Record<string, number>
  workstation_limits: Record<string, number>
  facilities: Record<string, number>
  suspicion: SuspicionState
  /** 已解锁科技里已接入结算的加成（模块 E5；口径见《数值平衡表》§6.4） */
  tech_effects: { catnip_efficiency: number }
  security: SecurityState
  launch_silo: LaunchSiloBlock
  offline_report: OfflineReport
}

export interface ScavengeResult {
  scrap: number
  scrap_max: number
  manual_scavenge_clicks: number
  clicks_left: number
  cold_start_finished: boolean
  hint: string | null
}

export interface HysteresisPolicy {
  enabled: boolean
  upper: number
  lower: number
  shift: number
}

export interface DispatchResult {
  role: string
  count: number
  unassigned: number
  total_cats: number
  workstations: Record<string, number>
  workstation_limits: Record<string, number>
  power_net_kw: number
  policy: HysteresisPolicy | null
}

export interface BuildResult {
  facility_id: string
  level: number
  count: number
  cost_paid: Record<string, number>
  resources: Resources
  caps: Record<string, number>
  total_cats: number
  unassigned: number
  cat_capacity: number
  workstation_limits: Record<string, number>
  power_net_kw: number
  narrative: string | null
  unlock_hint: { tech_id?: string; facility_id?: string } | null
}

export interface SnapshotResult {
  code: number
  message: string
  saved_at: number
}

export interface RadioItem {
  id: number
  category: 'RADIO_NEWS' | 'BBS_POST' | 'DISASTER_ALERT'
  text: string
  impact_stock: string | null
  template_text: string
  use_count: number
}

export interface RadioFeed {
  phase_id: string
  seeded: number
  pool_size: number
  items: RadioItem[]
}

export interface RadioGenerateResult {
  phase_id: string
  requested: number
  generated: number
  rejected: number
  source: 'LLM' | 'FALLBACK'
  note: string | null
  fell_back: boolean
  usage: {
    model: string
    ok: boolean
    attempts: number
    prompt_tokens: number
    completion_tokens: number
    duration_ms: number
    reason: string | null
  }
  budget: { day: string; calls: number; tokens: number; call_budget: number; exhausted: boolean }
  pool_size: number
}

export interface FacilityEffect {
  cat_capacity?: number
  breeding_bonus?: number
  workstation?: Record<string, number>
  power_gen_kw?: number
  power_load_kw?: number
  battery_kwh_max?: number
  unlock_system?: string
  noise_multiplier?: { first_level: number; per_extra_level: number; floor: number }
}

export interface FacilityDefinition {
  facility_id: string
  name: string
  role: string
  cost: Record<string, number>
  growth: number | null
  max_level: number | null
  buildable?: boolean
  effects: FacilityEffect
  unlock?: { tech_id?: string; facility_id?: string }
  description: string
}

export interface JobDefinition {
  job_id: string
  name: string
  era: string
  output: { resource: string | null; rate_per_second: number }
  workstation: { facility_id: string | null; slots_per_level: number; note?: string }
  unlock?: { tech_id?: string; facility_id?: string; tech_tier?: string }
  flavor: string
}

export interface LogEntry {
  id: number
  at: string
  text: string
  kind: 'info' | 'warn' | 'crit'
}

// ---- 模块 G：载具 / 机库 / 远征 ----
export type VehicleStatusValue = 'IDLE' | 'EXPEDITION' | 'REPAIR' | 'SCRAPPED'

export interface VehicleView {
  unit_id: number
  unit_type: string
  unit_name: string
  nickname: string | null
  modules: Array<{ slot: number; module_id: string }>
  shield: number
  armor: number
  armor_max: number
  hull: number
  crew_cats: number
  status: VehicleStatusValue
  repair_ends_at: number | null
  expedition_id: string | null
  combat_power: number
}

export interface VehicleTypeView {
  name: string
  cost: Record<string, number>
  crew: number
  hangar_slots: number
  shield: number
  armor: number
  hull: number
  dps: number
}

export interface ExpeditionTargetView {
  name: string
  duration_seconds: number
  suspicion_cost: number
  drops: Record<string, number>
  requires_unit_types: string[]
}

export interface ExpeditionEntry {
  expedition_id: string
  target_id: string
  unit_ids: number[]
  started_at: number
  ends_at: number
  collected: boolean
}

export interface HospitalEntry {
  unit_id: number
  cats: number
  ends_at: number
}

export interface HangarView {
  hangar_capacity: number
  hangar_used: number
  laser_turrets: number
  cruise_missiles: number
  decoy_count: number
  tactical_buff: { command: string; expires_at: number } | null
  convoy_ends_at: number | null
  factory_frozen_until: number | null
  threat_level: number
  rage: number
  fleet_strength: number
  raid_ends_at: number | null
  final_stage_cleared: number
  completed: boolean
  epitaph: string | null
  hospital_queue: HospitalEntry[]
  active_expeditions: ExpeditionEntry[]
  vehicles: VehicleView[]
  vehicle_types: Record<string, VehicleTypeView>
  expedition_targets: Record<string, ExpeditionTargetView>
}

export interface LootResult {
  expedition_id: string
  target_id: string
  gained: Record<string, number>
  overflowed: string[]
  suspicion_cost: number
  report: string[]
}
