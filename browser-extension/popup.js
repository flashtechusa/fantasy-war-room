/**
 * Fantasy War Room ESPN Connector -- popup logic.
 *
 * Design rules, all of them load-bearing:
 *
 * 1. Cookie values live in a local variable for the duration of one click and
 *    are never written to `chrome.storage`, `localStorage`, the DOM, or the
 *    console. The popup only ever renders "found" or "not found".
 * 2. Nothing happens without a click. Opening the popup checks whether the
 *    cookies exist; sending them requires the button.
 * 3. The only host permission granted up front is `fantasy.espn.com`. Talking
 *    to the user's own Fantasy War Room is an *optional* permission, requested
 *    at connect time for the exact origin they typed.
 * 4. The destination is the user's own server. There is no analytics, no
 *    telemetry and no third-party request anywhere in this file.
 */

const ESPN_COOKIE_URL = 'https://fantasy.espn.com'
const COOKIE_NAMES = ['SWID', 'espn_s2']
const SETTINGS_KEY = 'fwr.settings'

const els = {
  server: document.getElementById('server'),
  code: document.getElementById('code'),
  connect: document.getElementById('connect'),
  status: document.getElementById('status'),
  detected: document.getElementById('detected'),
}

/** Never holds a cookie value -- only whether each one was found. */
const detected = { swid: false, espn_s2: false, league_id: null, season: null }

function setRow(key, text, ok) {
  const row = els.detected.querySelector(`[data-key="${key}"] span`)
  if (!row) return
  row.textContent = text
  row.parentElement.classList.toggle('ok', Boolean(ok))
  row.parentElement.classList.toggle('missing', !ok)
}

function setStatus(text, kind = '') {
  els.status.textContent = text
  els.status.className = `status ${kind}`
}

function refreshButton() {
  const ready =
    detected.swid &&
    detected.espn_s2 &&
    els.code.value.trim().length >= 4 &&
    isUsableServer(els.server.value)
  els.connect.disabled = !ready
}

function isUsableServer(value) {
  try {
    const url = new URL(value.trim())
    if (url.protocol === 'https:') return true
    // Plain HTTP is allowed only for loopback. Anywhere else it would put a
    // live session credential on the wire in clear text.
    return (
      url.protocol === 'http:' &&
      ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
    )
  } catch {
    return false
  }
}

/**
 * Ask the browser whether ESPN's cookies exist, without keeping them.
 *
 * `chrome.cookies` is the only API that can see `espn_s2`: ESPN sets it
 * HttpOnly, so page JavaScript (and therefore any bookmarklet) cannot.
 */
async function checkCookies() {
  for (const name of COOKIE_NAMES) {
    let cookie = null
    try {
      cookie = await chrome.cookies.get({ url: ESPN_COOKIE_URL, name })
    } catch (error) {
      cookie = null
    }
    const key = name === 'SWID' ? 'swid' : 'espn_s2'
    detected[key] = Boolean(cookie && cookie.value)
    setRow(
      key,
      detected[key] ? 'found — you are signed in to ESPN' : 'not found — sign in to ESPN first',
      detected[key],
    )
  }
}

/** Pull the league id out of the ESPN page URL. It is not a secret. */
function leagueFromUrl(rawUrl) {
  try {
    const url = new URL(rawUrl)
    if (!url.hostname.endsWith('espn.com')) return { leagueId: null, season: null }
    const leagueId =
      url.searchParams.get('leagueId') || url.searchParams.get('leagueid')
    const season = url.searchParams.get('seasonId') || url.searchParams.get('season')
    return {
      leagueId: leagueId ? Number(leagueId) : null,
      season: season ? Number(season) : null,
    }
  } catch {
    return { leagueId: null, season: null }
  }
}

async function checkActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    const { leagueId, season } = leagueFromUrl(tab?.url || '')
    detected.league_id = leagueId
    detected.season = season
    setRow(
      'league',
      leagueId ? `league ${leagueId}${season ? ` · ${season}` : ''}` : 'none on this page (optional)',
      Boolean(leagueId),
    )
  } catch {
    setRow('league', 'none on this page (optional)', false)
  }
}

async function loadSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_KEY)
  const settings = stored[SETTINGS_KEY] || {}
  // Only the server address is remembered. Never a code, never a cookie.
  els.server.value = settings.server || 'http://localhost:8000'
}

async function saveServer(server) {
  await chrome.storage.local.set({ [SETTINGS_KEY]: { server } })
}

/** Request permission for the user's own server, at the moment it is needed. */
async function ensureServerPermission(origin) {
  const pattern = `${origin}/*`
  const already = await chrome.permissions.contains({ origins: [pattern] })
  if (already) return true
  return chrome.permissions.request({ origins: [pattern] })
}

async function connect() {
  els.connect.disabled = true
  setStatus('Connecting…')

  let server
  try {
    server = new URL(els.server.value.trim())
  } catch {
    setStatus('That address is not a valid URL.', 'error')
    refreshButton()
    return
  }

  if (!isUsableServer(server.href)) {
    setStatus('Use https, or http only for localhost.', 'error')
    refreshButton()
    return
  }

  const granted = await ensureServerPermission(server.origin)
  if (!granted) {
    setStatus('Permission to reach that address was declined.', 'error')
    refreshButton()
    return
  }

  // The cookies are read here, used once, and go out of scope immediately.
  let swid = null
  let espnS2 = null
  try {
    const swidCookie = await chrome.cookies.get({ url: ESPN_COOKIE_URL, name: 'SWID' })
    const s2Cookie = await chrome.cookies.get({ url: ESPN_COOKIE_URL, name: 'espn_s2' })
    swid = swidCookie?.value || null
    espnS2 = s2Cookie?.value || null

    if (!swid || !espnS2) {
      setStatus('ESPN cookies are missing. Sign in to ESPN and try again.', 'error')
      refreshButton()
      return
    }

    const body = {
      pairing_code: els.code.value.trim().toUpperCase(),
      swid,
      espn_s2: espnS2,
      client: `extension ${chrome.runtime.getManifest().version}`,
    }
    if (detected.league_id) body.league_id = detected.league_id
    if (detected.season) body.season = detected.season

    const response = await fetch(`${server.origin}/api/espn/extension/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // No cookies of ours travel with this; the pairing code is the auth.
      credentials: 'omit',
      body: JSON.stringify(body),
    })

    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`
      try {
        const payload = await response.json()
        if (payload?.detail) detail = payload.detail
      } catch {
        /* keep the status line */
      }
      setStatus(detail, 'error')
      refreshButton()
      return
    }

    await saveServer(server.origin)
    els.code.value = ''
    setStatus('Connected. Pick your league in Fantasy War Room.', 'ok')
  } catch (error) {
    // `error` can carry the request URL but never a cookie value: the values
    // are only ever in the request body, which is not part of the error.
    setStatus('Could not reach Fantasy War Room at that address.', 'error')
  } finally {
    swid = null
    espnS2 = null
    refreshButton()
  }
}

els.code.addEventListener('input', refreshButton)
els.server.addEventListener('input', refreshButton)
els.connect.addEventListener('click', connect)

;(async function init() {
  await loadSettings()
  await checkCookies()
  await checkActiveTab()
  refreshButton()
})()
