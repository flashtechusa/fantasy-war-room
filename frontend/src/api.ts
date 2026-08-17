/** Typed client for the Fantasy War Room API. */

export interface ScoreComponent {
  key: string
  label: string
  value: number
  weight: number
  contribution: number
  detail: string
}

export interface PlayerRow {
  espn_player_id: number
  name: string
  position: string
  pro_team: string
  bye_week: number | null
  rank: number
  position_rank: number
  tier: number
  projected_points: number
  points_per_game: number
  espn_projected_points: number
  vor: number
  replacement_points: number
  adp: number | null
  adp_delta: number
  espn_rank: number | null
  scarcity: number
  draft_score: number
  availability_next_pick: number
  gone_probability: number
  opportunity_cost: number
  wait_cost: number
  upside_points: number
  floor_points: number
  injury_status: string
  percent_owned: number
  projected_games: number
  value_grade: 'elite' | 'good' | 'fair' | 'reach'
  flags: string[]
  is_drafted: boolean
  drafted_by_me: boolean
  components?: ScoreComponent[]
  reasons?: string[]
  why_now?: string
  why_wait?: string
  principal_risk?: string
  scoring_breakdown?: {
    stat_id: number
    abbrev: string
    label: string
    stat_value: number
    points_per: number
    points: number
  }[]
}

export interface DraftPositionInfo {
  current_pick: number
  round: number
  pick_in_round: number
  on_the_clock_slot: number
  my_slot: number
  is_my_pick: boolean
  my_next_pick: number | null
  picks_until_my_next: number | null
  my_upcoming_picks: number[]
  rounds: number
  rounds_remaining: number
  team_count: number
  draft_type: string
}

export interface PositionScarcityInfo {
  position: string
  starters_left: number
  tier_players_left: number
  cliff_points: number
  picks_to_cliff: number | null
  dropoff_to_next_pick: number
  scarcity_score: number
  expected_best_next: number
  best_available_now: number
  explanation: string
}

export interface NeedInfo {
  position: string
  rostered: number
  starters_required: number
  starters_filled: number
  marginal_value: number
  need_score: number
  urgent: boolean
  note: string
}

export interface BoardMeta {
  scoring_format: string
  league_shape: string
  source: string
  market_drift: number
  current_pick: number
  next_pick: number | null
  picks_until_next: number | null
  position_decay: Record<string, number>
  scarcity: Record<string, PositionScarcityInfo>
  needs: Record<string, NeedInfo>
  replacement: {
    positions: Record<
      string,
      {
        position: string
        demand: number
        starter_demand: number
        flex_demand: number
        bench_demand: number
        replacement_points: number
        pool_size: number
        explanation: string
      }
    >
    flex_replacement_points: number
    flex_starter_bar: number
    flex_demand_total: number
    iterations: number
  }
}

export interface BoardResponse {
  total: number
  offset: number
  limit: number
  filter: string
  search: string | null
  players: PlayerRow[]
  draft_position: DraftPositionInfo
  meta: BoardMeta
}

export interface DraftPick {
  overall_pick: number
  round: number
  round_pick: number
  draft_slot: number
  espn_player_id: number
  player_name: string
  position: string
  pro_team: string
  is_mine: boolean
  source: string
}

export interface HistoricalPick {
  overall_pick: number
  round_num: number
  round_pick: number
  team_name: string
  espn_team_id: number | null
  player_name: string
  espn_player_id: number | null
  keeper: boolean
  bid_amount: number
}

export interface DraftState {
  id: number
  name: string
  my_draft_slot: number
  team_count: number
  rounds: number
  draft_type: string
  espn_sync_enabled: boolean
  last_synced_at: string | null
  picks_made: number
  picks: DraftPick[]
}

export interface Recommendations {
  best_pick: PlayerRow | null
  top_picks: PlayerRow[]
  draft_position: DraftPositionInfo
  meta: BoardMeta
}

export interface LeagueInfo {
  id: number
  espn_league_id: number
  season: number
  name: string
  source: string
  is_demo: boolean
  imported_at: string
  team_count: number
  scoring: {
    type: string
    format_label: string
    is_ppr: boolean
    ppr_value: number
    points_per_passing_td: number
    rules: {
      stat_id: number
      abbrev: string
      label: string
      points: number
      points_overrides: Record<string, number>
    }[]
    rule_count: number
  }
  roster: {
    starting_slots: Record<string, number>
    all_slot_counts: Record<string, number>
    bench_slots: number
    ir_slots: number
    starters_total: number
    roster_size: number
    draft_rounds: number
    flex_slots: { label: string; count: number; eligible: string[] }[]
    is_superflex: boolean
    shape_summary: string
  }
  draft: {
    type: string
    order: number[]
    completed: boolean
    keeper_count: number
    date: number | null
    seconds_per_pick: number | null
  }
  waivers: {
    type: string
    uses_faab: boolean
    budget: number
    process_days: string[]
    waiver_hours: number | null
    acquisition_limit: number | null
  }
  playoffs: {
    regular_season_weeks: number
    team_count: number
    matchup_length: number
    seed_tie_rule: string
  }
  trades: { deadline: number; veto_votes_required: number }
  teams: {
    espn_team_id: number
    name: string
    abbrev: string
    owners: string[]
    logo_url: string
    division_name: string
    draft_slot: number | null
    is_mine: boolean
    record: { wins: number; losses: number; ties: number }
    roster: { name: string; position: string; slot: string; pro_team: string }[]
    roster_size: number
  }[]
}

export interface HealthInfo {
  status: string
  season: number
  demo_mode: boolean
  espn: {
    league_id_configured: boolean
    private_credentials_configured: boolean
    reachable_config: boolean
  }
  league_imported: boolean
  league: { name: string; season: number; source: string; team_count: number } | null
  players_loaded: number
  leagues_stored: number
}

export interface LineupPlayer {
  espn_player_id: number
  name: string
  position: string
  pro_team: string
  projected_points: number
  vor: number
  bye_week: number | null
  injury_status: string
}

export interface LeagueTeamRow {
  espn_team_id: number
  name: string
  owners: string[]
  is_mine: boolean
  roster_size: number
  rank: number
  construction_rank: number
  roster_construction_score: number
  roster_construction_notes: string[]
  projected_starter_points: number
  weekly_points: number
  positional_strength: {
    position: string
    starters_points: number
    league_average_points: number
    edge: number
    grade: string
  }[]
  strengths: string[]
  weaknesses: string[]
  bye_conflicts: { week: number; players: string[]; positions: string[]; severity: string }[]
}

export interface LeagueTeamsResponse {
  teams: LeagueTeamRow[]
  team_count: number
  league_median_points: number
  league_median_score: number
  note: string
}

export interface TeamResponse {
  picks_made: number
  roster_source: 'espn' | 'draft'
  my_team_identified: boolean
  lineup: {
    starters: { slot: string; player: LineupPlayer | null }[]
    bench: LineupPlayer[]
    total_points: number
    weekly_points: number
    empty_slots: string[]
  }
  positional_strength: {
    position: string
    starters_points: number
    league_average_points: number
    edge: number
    grade: string
  }[]
  strengths: string[]
  weaknesses: string[]
  bye_conflicts: { week: number; players: string[]; positions: string[]; severity: string }[]
  remaining_needs: NeedInfo[]
  highest_marginal_value: { position: string; marginal_value: number; note: string } | null
  roster_construction_score: number
  roster_construction_notes: string[]
  draft_position: DraftPositionInfo
}

export interface SimulationResponse {
  simulations: number
  my_slot: number
  team_count: number
  rounds: number
  seed: number
  expected_roster_points: { mean: number; p10: number; p90: number }
  mean_starter_points_by_position: Record<string, number>
  strategy: string[]
  picks: {
    overall_pick: number
    round: number
    position_share: Record<string, number>
    expected_best_vor: Record<string, number>
    mean_taken_vor: number
    likely_available: { name: string; position: string; probability: number }[]
  }[]
  wait_profiles: {
    position: string
    by_round: number[]
    cliff_round: number | null
    verdict: string
    note: string
  }[]
}

export interface WeekPlayer {
  espn_player_id: number
  name: string
  position: string
  pro_team: string
  week_points: number
  season_points: number
  vor: number
  bye_week: number | null
  injury_status: string
  percent_owned: number
  on_bye: boolean
  week_projection_is_real: boolean
}

export interface LineupResponse {
  week: number
  projected_points: number
  points_vs_naive: number
  starters: {
    slot: string
    player: WeekPlayer | null
    next_best: WeekPlayer | null
    margin: number
    close_call: boolean
    warning: string
    reason: string
  }[]
  bench: WeekPlayer[]
  warnings: string[]
  close_calls: { slot: string; starting: string | null; over: string | null; margin: number }[]
  unfilled_slots: string[]
  estimated_projections: string[]
}

export interface WaiverResponse {
  week: number
  roster_size: number
  roster_is_full: boolean
  free_agents_considered: number
  uses_faab: boolean
  faab_budget: number
  targets: {
    player: WeekPlayer
    week_gain: number
    season_gain: number
    drop: WeekPlayer | null
    priority: number
    faab_bid: number
    faab_pct: number
    verdict: string
    reasons: string[]
  }[]
}

export interface TradeSide {
  label: string
  gives: WeekPlayer[]
  gets: WeekPlayer[]
  week_before: number
  week_after: number
  season_before: number
  season_after: number
  week_delta: number
  season_delta: number
  roster_change: number
  position_changes: Record<string, number>
  notes: string[]
}

export interface TradeResponse {
  week: number
  verdict: string
  summary: string
  reasons: string[]
  my_side: TradeSide
  their_side: TradeSide | null
}

export interface SeasonRoster {
  week: number
  players: WeekPlayer[]
  teams: { espn_team_id: number; name: string; is_mine: boolean }[]
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

export interface ConfigInfo {
  espn_league_id: number | null
  espn_season: number
  demo_mode: boolean
  my_team_id: number | null
  my_draft_slot: number | null
  faab_remaining: number | null
  swid_set: boolean
  espn_s2_set: boolean
  has_private_credentials: boolean
  ready_for_espn: boolean
  sources: Record<string, string>
}

export interface ConfigSaveResult {
  saved: boolean
  config: ConfigInfo
  connection: { connected: boolean; league_name?: string; detail?: string } | null
}

export const api = {
  health: () => request<HealthInfo>('/api/health'),

  config: () => request<ConfigInfo>('/api/config'),

  version: () =>
    request<{
      git: boolean
      branch?: string
      commit?: string
      committed?: string
      updates_available?: number
      detail?: string
    }>('/api/system/version'),
  updateApp: () =>
    request<{ updated: boolean; commit: string; detail: string }>('/api/system/update', {
      method: 'POST',
    }),
  saveConfig: (body: Record<string, unknown>) =>
    request<ConfigSaveResult>('/api/config', { method: 'PUT', body: JSON.stringify(body) }),
  resetConfig: () => request<{ config: ConfigInfo }>('/api/config', { method: 'DELETE' }),
  espnHealth: () =>
    request<{ connected: boolean; demo_mode: boolean; detail?: string; league_name?: string }>(
      '/api/health/espn',
    ),

  league: () => request<LeagueInfo>('/api/league'),
  importLeague: () =>
    request<{ imported: boolean; players_imported: number; league: LeagueInfo }>(
      '/api/league/import',
      { method: 'POST', body: JSON.stringify({ include_players: true, include_history: true }) },
    ),
  refreshPlayers: () =>
    request<{ players_imported: number }>('/api/league/players/refresh', { method: 'POST' }),
  history: () =>
    request<{ seasons: number[]; picks_by_season: Record<string, HistoricalPick[]> }>(
      '/api/league/history',
    ),

  board: (params: {
    position?: string
    search?: string
    limit?: number
    offset?: number
    detail?: boolean
    include_drafted?: boolean
  }) => {
    const query = new URLSearchParams()
    if (params.position) query.set('position', params.position)
    if (params.search) query.set('search', params.search)
    query.set('limit', String(params.limit ?? 100))
    query.set('offset', String(params.offset ?? 0))
    if (params.detail) query.set('detail', 'true')
    if (params.include_drafted) query.set('include_drafted', 'true')
    return request<BoardResponse>(`/api/players?${query.toString()}`)
  },
  player: (id: number) => request<PlayerRow>(`/api/players/${id}`),

  draft: () => request<DraftState>('/api/draft'),
  recommendations: (limit = 10) =>
    request<Recommendations>(`/api/draft/recommendations?limit=${limit}`),
  pick: (espnPlayerId: number, overallPick?: number) =>
    request<{ draft: DraftState }>('/api/draft/pick', {
      method: 'POST',
      body: JSON.stringify({ espn_player_id: espnPlayerId, overall_pick: overallPick ?? null }),
    }),
  undo: () => request<{ draft: DraftState }>('/api/draft/undo', { method: 'POST' }),
  reset: () => request<{ draft: DraftState }>('/api/draft/reset', { method: 'POST' }),
  setSlot: (slot: number) =>
    request<{ draft: DraftState }>('/api/draft/slot', {
      method: 'POST',
      body: JSON.stringify({ my_draft_slot: slot }),
    }),
  sync: () =>
    request<{ synced: boolean; added?: number; reason?: string; draft: DraftState }>(
      '/api/draft/sync',
      { method: 'POST' },
    ),

  team: () => request<TeamResponse>('/api/team'),

  leagueTeams: () => request<LeagueTeamsResponse>('/api/team/league'),

  lineup: (week?: number) =>
    request<LineupResponse>(`/api/season/lineup${week ? `?week=${week}` : ''}`),
  waivers: (week?: number, limit = 15) => {
    const query = new URLSearchParams({ limit: String(limit) })
    if (week) query.set('week', String(week))
    return request<WaiverResponse>(`/api/season/waivers?${query.toString()}`)
  },
  refreshWaivers: (week?: number) =>
    request<{ free_agents_imported: number }>(
      `/api/season/waivers/refresh${week ? `?week=${week}` : ''}`,
      { method: 'POST' },
    ),
  seasonRoster: (week?: number) =>
    request<SeasonRoster>(`/api/season/roster${week ? `?week=${week}` : ''}`),
  analyseTrade: (body: {
    give: number[]
    receive: number[]
    their_team_id?: number | null
    week?: number | null
  }) => request<TradeResponse>('/api/season/trade', { method: 'POST', body: JSON.stringify(body) }),

  simulate: (body: { my_slot?: number; simulations?: number; seed?: number }) =>
    request<SimulationResponse>('/api/simulate', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}
