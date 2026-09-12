import type {
  ApiEnvelope,
  BuildResult,
  ColonyStateData,
  DispatchResult,
  HysteresisPolicy,
  Resources,
  ScavengeResult,
  SnapshotResult,
  RadioFeed,
  RadioGenerateResult,
} from '@/types/game'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'
const STATIC_BASE = import.meta.env.VITE_STATIC_BASE ?? '/static'

export class ApiRequestError extends Error {
  constructor(
    readonly code: number,
    message: string,
    readonly detail?: string,
  ) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (error) {
    throw new ApiRequestError(0, 'NETWORK_ERROR', String(error))
  }

  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new ApiRequestError(
      payload?.code ?? response.status,
      payload?.message ?? 'REQUEST_FAILED',
      payload?.detail,
    )
  }
  return payload as T
}

async function fetchStatic<T>(file: string): Promise<T> {
  const response = await fetch(`${STATIC_BASE}/${file}`)
  if (!response.ok) throw new ApiRequestError(response.status, 'STATIC_LOAD_FAILED', file)
  return (await response.json()) as T
}

export interface SnapshotPayload {
  slot: number
  planet_id: number | null
  client_time: number
  resources: Partial<Resources>
  population: Record<string, number>
  workstations: Record<string, number>
}

export const api = {
  getState: (slot: number) =>
    request<ApiEnvelope<ColonyStateData>>(`/colony/state?slot=${slot}`),

  snapshot: (payload: SnapshotPayload) =>
    request<SnapshotResult>('/colony/snapshot', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  scavenge: (slot: number) =>
    request<ApiEnvelope<ScavengeResult>>(`/colony/scavenge?slot=${slot}`, { method: 'POST' }),

  dispatch: (payload: {
    slot: number
    role: string
    delta: number
    policy?: HysteresisPolicy
  }) =>
    request<ApiEnvelope<DispatchResult>>('/colony/dispatch', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  build: (payload: { slot: number; facility_id: string; count?: number }) =>
    request<ApiEnvelope<BuildResult>>('/facilities/build', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  loadFacilityDefinitions: () =>
    fetchStatic<{ facilities: unknown[] }>('facilities.json').then((data) => data.facilities),

  loadJobDefinitions: () =>
    fetchStatic<{ jobs: unknown[] }>('jobs.json').then((data) => data.jobs),

  radioFeed: (slot: number, limit = 12) =>
    request<ApiEnvelope<RadioFeed>>(`/radio/feed?slot=${slot}&limit=${limit}`),

  radioGenerate: (payload: { slot: number; count?: number }) =>
    request<ApiEnvelope<RadioGenerateResult>>('/radio/generate', {
      method: 'POST',
      body: JSON.stringify({ count: 6, ...payload }),
    }),
}
