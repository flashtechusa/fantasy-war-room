# ESPN league details bookmarklet

## What it is for

Removing the "find your league id" step. It reads the league id, season and
team id out of the ESPN page URL and tells you which cookies your browser is
willing to hand to page JavaScript.

## What it cannot do, and why

It cannot read `espn_s2`.

ESPN sets `espn_s2` with the `HttpOnly` flag. `HttpOnly` means exactly one
thing: the browser will attach the cookie to requests it makes to ESPN, and
will refuse to expose it to JavaScript running on the page. `document.cookie`
does not contain it, and there is no API, trick or workaround that makes it
appear — that is the entire purpose of the flag, and it is the same mechanism
that stops a cross-site scripting bug on espn.com from stealing your session.

So:

- A bookmarklet cannot read `espn_s2`.
- A helper page you paste a URL into cannot read `espn_s2`.
- A `<script>` on any site cannot read `espn_s2`.

`SWID` is normally *not* HttpOnly and so is usually readable, but ESPN has
changed cookie flags before, so the bookmarklet reports what it actually finds
instead of assuming.

If you want the cookies collected automatically, use the browser extension in
the parent directory: `chrome.cookies` is the only browser API allowed to read
an HttpOnly cookie, and it requires an explicit permission grant. Otherwise,
copy them by hand from DevTools → Application → Cookies → `espn.com`.

## Install

Create a bookmark whose URL is the line below (source: `bookmarklet.js`).

```
javascript:(function(){var u=new URL(location.href),p=u.searchParams;function m(r){var f=u.pathname.match(r);return f?f[1]:null}var l=p.get('leagueId')||p.get('leagueid')||m(/\/leagues?\/(\d+)/i),s=p.get('seasonId')||p.get('season')||m(/\/seasons?\/(\d{4})/i),t=p.get('teamId')||p.get('teamid'),v={};document.cookie.split(';').forEach(function(c){var i=c.indexOf('=');if(i>0)v[c.slice(0,i).trim()]=true});var o=['Fantasy War Room -- ESPN league details','','League id: '+(l||'not found'),'Season:    '+(s||'not found'),'Team id:   '+(t||'not found'),'','Readable by page JavaScript:','  SWID:    '+(v.SWID?'readable':'NOT readable'),'  espn_s2: '+(v.espn_s2?'readable':'NOT readable (HttpOnly by design)')];if(!v.espn_s2)o.push('','espn_s2 is HttpOnly. No bookmarklet can read it. Use the Fantasy War Room extension, or copy it from DevTools > Application > Cookies.');prompt(o.join('\n'),l||'')})()
```

Open your ESPN league and click it. The prompt box pre-fills with your league
id so you can copy it straight into Fantasy War Room's manual entry.

## What it sends

Nothing. There is no network request in the bookmarklet at all — it reads the
URL, reads cookie *names*, and shows you a dialog.
