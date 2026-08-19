/**
 * ESPN auth probe — runs the bearer-vs-cookie matrix from inside espn.com.
 *
 * WHY THIS EXISTS
 * ---------------
 * `scratch/espn_auth_matrix.py` answers the same question but needs a terminal,
 * Python, and three credentials pulled out of DevTools. None of that exists on
 * a phone. This does, because of one property:
 *
 *   JavaScript running on an ESPN origin never has to *read* `espn_s2`.
 *   `fetch(url, {credentials: 'include'})` makes the browser attach it —
 *   HttpOnly and all — without exposing the value to script.
 *
 * So the cookie rows work on a phone even though the cookie is unreadable, and
 * no credential is ever extracted, copied, or transmitted anywhere.
 *
 * WHAT IT CANNOT SETTLE
 * ---------------------
 * Adding an `Authorization` header to a cross-origin request triggers a CORS
 * preflight. If ESPN's API does not allow that header from this origin, the
 * request dies in the browser *before* ESPN ever sees it — which is not the
 * same as ESPN rejecting the auth. Those rows are reported as INCONCLUSIVE
 * rather than as a failure, because scoring a CORS block as "bearer rejected"
 * would answer the question wrongly.
 *
 * A `401` is a real answer. `Failed to fetch` is not.
 *
 * PRIVACY
 * -------
 * Every request goes to an espn.com host and nowhere else. Nothing is stored,
 * nothing is logged, and the token — if one is found — is shown only as a short
 * prefix so you can tell which key it came from without putting the value on
 * screen. Close the overlay and nothing remains.
 *
 * USAGE
 * -----
 * See README.md in this directory for the one-line version and how to install
 * a bookmarklet on a phone. Run it while viewing your league on
 * fantasy.espn.com.
 */
(function espnAuthProbe() {
  var READ_HOST = 'https://lm-api-reads.fantasy.espn.com';
  var FAN_HOST = 'https://fan.api.espn.com';

  // ---- find the league on this page ------------------------------------
  var url = new URL(location.href);
  var leagueId =
    url.searchParams.get('leagueId') ||
    url.searchParams.get('leagueid') ||
    (location.pathname.match(/\/leagues?\/(\d+)/i) || [])[1];
  var season =
    url.searchParams.get('seasonId') ||
    url.searchParams.get('season') ||
    new Date().getFullYear();

  if (!leagueId) {
    leagueId = prompt('League id? (open your league page first)');
    if (!leagueId) return;
  }

  // ---- hunt for a OneID token in client-visible storage -----------------
  // If Disney's web SDK keeps the access token where page script can see it,
  // a phone can obtain it. If not, the bearer rows simply cannot be tested
  // from here, which is itself worth knowing.
  // A JWT's first segment is a base64url header. `{"alg":"RS256"}` encodes to
  // just 20 characters, so a stricter length test silently misses real tokens
  // and reports "none found" -- which reads as a finding rather than a miss.
  var JWT_SHAPE = /^ey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\./;

  function findToken() {
    var hits = [];
    [localStorage, sessionStorage].forEach(function (store, storeIndex) {
      var name = storeIndex === 0 ? 'localStorage' : 'sessionStorage';
      for (var i = 0; i < store.length; i++) {
        var key = store.key(i);
        var raw = store.getItem(key) || '';
        if (raw.length > 5000) continue;
        var found = null;
        if (JWT_SHAPE.test(raw)) {
          found = raw; // a bare JWT
        } else if (raw.charAt(0) === '{' || raw.charAt(0) === '[') {
          // Search every JSON value, not just keys whose *name* looks
          // auth-ish. Disney's storage schema is unknown to us, so keying off
          // the name would report "no token" for a token sitting in plain
          // sight under an unrecognised key -- a false negative that reads as
          // a real finding.
          try {
            found = deepFindToken(JSON.parse(raw));
          } catch (e) {
            found = null;
          }
        }
        if (found) hits.push({ store: name, key: key, token: found });
      }
    });
    return hits;
  }

  function deepFindToken(node, depth) {
    depth = depth || 0;
    if (depth > 6 || node == null) return null;
    if (typeof node === 'string') {
      return JWT_SHAPE.test(node) ? node : null;
    }
    if (typeof node !== 'object') return null;
    var preferred = ['access_token', 'accessToken', 'id_token', 'idToken'];
    for (var p = 0; p < preferred.length; p++) {
      var v = node[preferred[p]];
      if (typeof v === 'string' && v.length > 20) return v;
    }
    for (var k in node) {
      if (!Object.prototype.hasOwnProperty.call(node, k)) continue;
      var hit = deepFindToken(node[k], depth + 1);
      if (hit) return hit;
    }
    return null;
  }

  var tokenHits = findToken();
  var token = tokenHits.length ? tokenHits[0].token : null;

  // ---- the matrix -------------------------------------------------------
  var leagueUrl =
    READ_HOST +
    '/apis/v3/games/ffl/seasons/' + season +
    '/segments/0/leagues/' + leagueId + '?view=mSettings';

  var modes = [
    { label: 'Cookie only (baseline)', creds: 'include', auth: null },
    { label: 'No auth (control)', creds: 'omit', auth: null },
    { label: 'Bearer only', creds: 'omit', auth: 'Bearer ' },
    { label: 'Bearer + cookies', creds: 'include', auth: 'Bearer ' },
    { label: 'Bare token', creds: 'omit', auth: '' },
    { label: 'APIKEY token', creds: 'omit', auth: 'APIKEY ' }
  ];

  function classify(body) {
    if (!body || typeof body !== 'object') return 'not JSON';
    var s = body.settings || {};
    if (s.name && (body.teams || []).length) return 'REAL DATA';
    if (s.name || body.status) return 'partial';
    return 'shell';
  }

  function probe(mode) {
    if (mode.auth !== null && !token) {
      return Promise.resolve({ mode: mode.label, status: 'skipped',
        note: 'no token in page storage' });
    }
    var init = { method: 'GET', credentials: mode.creds, headers: {} };
    if (mode.auth !== null) {
      init.headers.Authorization = (mode.auth + token).trim();
    }
    return fetch(leagueUrl, init)
      .then(function (res) {
        return res.json().catch(function () { return null; })
          .then(function (body) {
            return { mode: mode.label, status: res.status, note: classify(body) };
          });
      })
      .catch(function (err) {
        // No status means the browser refused it, not that ESPN did.
        return {
          mode: mode.label,
          status: 'BLOCKED',
          note: mode.auth !== null
            ? 'CORS preflight — INCONCLUSIVE, not a rejection'
            : String(err && err.message || err)
        };
      });
  }

  // ---- render, phone-sized ---------------------------------------------
  function render(rows) {
    var old = document.getElementById('fwr-auth-probe');
    if (old) old.remove();

    var baseline = rows[0];
    var control = rows[1];
    var bearerRows = rows.slice(2);
    var conclusive = bearerRows.filter(function (r) {
      return r.status !== 'BLOCKED' && r.status !== 'skipped';
    });
    var passing = conclusive.filter(function (r) {
      return r.status === 200 && r.note === 'REAL DATA';
    });

    var verdict;
    if (baseline.status !== 200 || baseline.note !== 'REAL DATA') {
      verdict = 'INVALID — baseline failed. Are you signed in to ESPN and on your league page?';
    } else if (control.status === 200 && control.note === 'REAL DATA') {
      verdict = 'INVALID — this league is PUBLIC, so it cannot test authentication.';
    } else if (!token) {
      verdict = 'NO TOKEN found in page storage — the bearer rows could not be tested. ' +
        'That itself suggests the web client does not keep a bearer token client-side.';
    } else if (!conclusive.length) {
      verdict = 'INCONCLUSIVE — every bearer row was blocked by CORS before ESPN saw it. ' +
        'Needs the Python script from a desktop.';
    } else if (passing.length) {
      verdict = 'YES — this backend accepts bearer auth (' + passing[0].mode + ').';
    } else {
      verdict = 'NO — bearer rejected with a real status code. Cookie-only.';
    }

    var html =
      '<div style="font:13px/1.45 system-ui;padding:14px;color:#eee">' +
      '<div style="display:flex;justify-content:space-between;align-items:center">' +
      '<strong style="font-size:15px">ESPN auth probe</strong>' +
      '<button id="fwr-close" style="background:#333;color:#eee;border:0;' +
      'border-radius:6px;padding:6px 12px;font-size:14px">Close</button></div>' +
      '<div style="color:#9aa;margin:6px 0 10px">league ' + leagueId +
      ' · ' + season + ' · token ' +
      (token ? 'found in ' + tokenHits[0].store + ' (' + tokenHits[0].key + ')' : 'not found') +
      '</div><table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<tr style="color:#9aa"><th align="left">Mode</th><th align="left">Status</th>' +
      '<th align="left">Body</th></tr>';

    rows.forEach(function (r) {
      var colour = r.status === 200 ? '#3fb950'
        : r.status === 'BLOCKED' ? '#d29922' : '#f85149';
      html += '<tr style="border-top:1px solid #333">' +
        '<td style="padding:5px 0">' + r.mode + '</td>' +
        '<td style="color:' + colour + '">' + r.status + '</td>' +
        '<td style="color:#9aa">' + r.note + '</td></tr>';
    });

    html += '</table><div style="margin-top:12px;padding:10px;background:#1a1d24;' +
      'border-radius:6px;font-size:13px"><strong>' + verdict + '</strong></div>' +
      '<div style="color:#667;margin-top:8px;font-size:11px">' +
      'Screenshot this. No credential left this page.</div></div>';

    var panel = document.createElement('div');
    panel.id = 'fwr-auth-probe';
    panel.setAttribute(
      'style',
      'position:fixed;inset:auto 0 0 0;max-height:85vh;overflow:auto;z-index:2147483647;' +
      'background:#0f1115;border-top:2px solid #d63b26;box-shadow:0 -4px 20px rgba(0,0,0,.6)'
    );
    panel.innerHTML = html;
    document.body.appendChild(panel);
    document.getElementById('fwr-close').onclick = function () { panel.remove(); };
  }

  // Sequential rather than parallel: six simultaneous requests to the same
  // endpoint is exactly the shape that earns a 429, which would corrupt the
  // result with rate-limit noise.
  var rows = [];
  modes.reduce(function (chain, mode) {
    return chain.then(function () {
      return probe(mode).then(function (r) { rows.push(r); });
    });
  }, Promise.resolve()).then(function () { render(rows); });
})();
