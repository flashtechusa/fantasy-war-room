/**
 * Fantasy War Room -- ESPN league details bookmarklet.
 *
 * WHAT THIS CANNOT DO
 * -------------------
 * It cannot read `espn_s2`. ESPN sets that cookie `HttpOnly`, which means the
 * browser withholds it from `document.cookie` by design. No bookmarklet, no
 * injected script, and no page-level JavaScript of any kind can read it. That
 * is not a limitation of this code -- it is the security boundary working.
 *
 * A tool that claimed otherwise would either be wrong or be exfiltrating the
 * cookie some other way, so this one says so plainly and stops.
 *
 * WHAT IT CAN DO
 * --------------
 * The parts of your setup that are *not* credentials:
 *   - league id and season, from the page URL
 *   - your team id, when the page you are on identifies it
 *   - whether `SWID` happens to be readable (it is usually not HttpOnly, but
 *     ESPN has changed this before, so it is reported rather than assumed)
 *
 * That is genuinely useful: it removes the "find your league id" step, which
 * is the part people get wrong. It does not remove the cookie step. For that,
 * use the browser extension in the directory above, which uses the
 * `chrome.cookies` API -- the only interface permitted to read an HttpOnly
 * cookie, and only with an explicit permission grant.
 *
 * INSTALL
 * -------
 * Make a new bookmark whose URL is `javascript:` followed by the minified
 * contents of this file (see README.md for the one-line version), open your
 * ESPN league, and click it.
 */
(function fantasyWarRoomInspect() {
  var url = new URL(window.location.href)
  var params = url.searchParams

  function firstMatch(patterns) {
    for (var i = 0; i < patterns.length; i++) {
      var found = url.pathname.match(patterns[i])
      if (found) return found[1]
    }
    return null
  }

  var leagueId =
    params.get('leagueId') ||
    params.get('leagueid') ||
    firstMatch([/\/leagues?\/(\d+)/i])

  var season =
    params.get('seasonId') ||
    params.get('season') ||
    firstMatch([/\/seasons?\/(\d{4})/i])

  var teamId = params.get('teamId') || params.get('teamid')

  // `document.cookie` shows only cookies that are NOT HttpOnly. Whatever is
  // missing here is missing because the browser is refusing to hand it over.
  var visible = {}
  document.cookie.split(';').forEach(function (pair) {
    var index = pair.indexOf('=')
    if (index > 0) visible[pair.slice(0, index).trim()] = true
  })

  var lines = [
    'Fantasy War Room -- ESPN league details',
    '',
    'League id: ' + (leagueId || 'not found on this page'),
    'Season:    ' + (season || 'not found on this page'),
    'Team id:   ' + (teamId || 'not found on this page'),
    '',
    'Cookies readable by page JavaScript:',
    '  SWID:     ' + (visible.SWID ? 'readable' : 'NOT readable (HttpOnly or absent)'),
    '  espn_s2:  ' + (visible.espn_s2 ? 'readable' : 'NOT readable (HttpOnly by design)'),
    '',
  ]

  if (!visible.espn_s2) {
    lines.push(
      'espn_s2 is HttpOnly, so no bookmarklet can read it. To connect without',
      'copying cookies by hand, install the Fantasy War Room browser extension.',
      'To do it by hand: DevTools > Application > Cookies > espn.com.',
    )
  }

  // A prompt() rather than a copy to the clipboard: nothing here is written
  // anywhere the user did not choose to put it.
  window.prompt(lines.join('\n'), leagueId ? String(leagueId) : '')
})()
