# ESPN connection — emergency runbook

**Purpose: operational continuity.** If Disney changes the OTP login flow in the
middle of fantasy season, this document is the fallback procedure that still
gets a user connected the same day. It is written to be followed under pressure,
so it repeats itself and spells things out.

Every method below ends in the **same place**: the app stores two ESPN cookies,
`SWID` and `espn_s2`, encrypted, and everything afterward (league discovery,
team detection, import, draft sync, disconnect) runs on the one shared ESPN
layer. The methods differ only in how those two cookies are obtained.

Connection methods the app offers, in order:

1. [ESPN Email Code (OTP)](#1-espn-email-code-otp--primary) — primary
2. [Public League Link](#2-public-league-link--fallback) — fallback
3. [Manual SWID + espn_s2](#4-manual-swid--espn_s2--permanent-last-resort) — permanent last resort

And one method the app does **not** show a user, kept for maintainers only:

- [Browser Extension](#3-browser-extension--desktop-backup) — developer-mode
  proof-of-concept, loaded unpacked; not installable, so it is not a UI option.

> Screenshots to add later are flagged `[SCREENSHOT: ...]`. They are not required
> to follow the steps, but will help less technical users.

---

## 1. ESPN Email Code (OTP) — primary

The method to try first. Works on any device, for public and private leagues,
with no password and no DevTools.

**User steps**

1. Connect ESPN → **ESPN Email Code**.
2. Enter your ESPN email → **Send ESPN code**.
3. ESPN emails a six-digit code. Enter it → **Verify & connect**.
4. The app picks up from "We found your leagues" — choose one and import.

`[SCREENSHOT: email + code entry]`

**Server prerequisite: none.** The real Disney recovery flow carries no API key,
so this method needs no configuration. `FWR_ESPN_OTP_ENABLED=0` is a kill switch
that hides it (falling back to Public/Manual) if Disney's flow ever misbehaves —
on by default.

**If it stops working entirely** (Disney changed the flow): the app surfaces a
redacted error naming the step that broke (`recovery_methods`, `request_otp`,
`submit_otp`, `establish`). Run `python scripts/test_espn_otp.py --show-shapes`
from a machine that can reach Disney; it prints the redacted response structure
for the broken step so the observed contract in `backend/app/espn/oneid.py` can
be corrected. **Meanwhile, tell users to use Method 4 (Manual).** That is the
whole reason Manual is permanent.

The four calls, for reference (all POST under
`/jgc/v8/client/ESPN-ONESITE.WEB-PROD`):

1. `/guest/recovery-methods` — `{loginValue}` → confirms the account
2. `/notification/otp/recovery` — `{lookupValue}` → emails the code, returns a session id
3. `/otp/redeem` — `{passcode, sessionIds[]}` → returns `swid` + a recovery token
4. `/guest/login/recoveryToken?expand=s2…` — `{swid, recoveryToken}` → returns
   `data.s2` (= espn_s2) and `data.profile.swid` (= SWID)

The app keeps only `SWID` + `espn_s2` and discards every other token.

**Common messages**

| Message | Meaning | Fix |
|---|---|---|
| "That does not look like an email address." | Typo in the email | Re-enter |
| "ESPN sent a login code…" then no email | Wrong email, or ESPN delay | Check spam; "Use a different email"; wait a minute |
| "That code request has expired. Start again." | >10 minutes since the code was sent | Start again — codes and flows expire in 10 min |
| "OneID … failed" | Disney rejected the step | See "If it stops working" above |
| Method not shown at all | `FWR_ESPN_OTP_ENABLED=0` (kill switch) | Use Public or Manual, or re-enable it |

---

## 2. Public League Link — fallback

For **public** leagues only. No sign-in, works on a phone. Ownership is marked
**Unverified** because the app cannot confirm the user's ESPN identity this way.

**User steps**

1. Connect ESPN → **Public League Link**.
2. Paste the ESPN league URL (or just the league id) and the season.
   - The app extracts the id from `…/league?leagueId=123456`.
3. **Find this league** → select your team → import.

`[SCREENSHOT: paste league URL]`

**If the league is private**, the app says so and returns nothing to import —
move the user to Email Code or Manual. A private league returns HTTP 401 without
credentials; that is not an outage, it is the league being private.

---

## 3. Browser Extension — desktop backup

**Status, stated plainly:** a Manifest V3 **proof-of-concept**, not a published
Chrome Web Store extension. Chrome/Edge on desktop only. It has unit tests for
its payload contract but has **not** been proven end-to-end against a live
backend. Keep it as a backup, do not present it as a shipping product.

**Where it lives:** `browser-extension/` in this repository — `manifest.json`,
`popup.html`, `popup.js`, `popup.css`, `icon128.png`.

**What it does:** reads ESPN's `SWID` and `espn_s2` cookies (via the privileged
`chrome.cookies` API — the only browser API that can read the HttpOnly
`espn_s2`), reads the league id from the current ESPN tab, and POSTs them to
`POST /api/espn/extension/connect` on the server, authenticated by a single-use
pairing code the user generates in the app. That endpoint exists and is
whitelisted public in `backend/app/main.py`.

**How to test it right now (developer mode)**

1. In the app: Connect ESPN → **Browser Extension** → **Generate pairing code**.
2. Chrome/Edge → `chrome://extensions` → enable **Developer mode**.
3. **Load unpacked** → select the `browser-extension/` folder.
4. Open your ESPN league in a tab, signed in.
5. Click the extension → enter your server address and the pairing code →
   **Connect this ESPN account**.
6. Back in the app, the connection should now be stored; pick your league.

`[SCREENSHOT: chrome://extensions Load unpacked]`
`[SCREENSHOT: extension popup]`

**What it does NOT do yet:** it is not packaged (`.zip`), not submitted to any
store, has no privacy-policy URL, and its `optional_host_permissions` still
include `https://*/*` (fine for dev, must be pinned to the real domain before
any submission). Turning it into a normal installable extension requires:
pin the host permission to the production domain, add 16/32/48 icons, host a
privacy policy, write the store listing and screenshots, package, pay the one
-time developer fee, and pass review.

---

## 4. Manual SWID + espn_s2 — permanent last resort

**This must always work and always be available.** It depends on nothing but a
desktop browser, so it is the floor under every other method. When Email Code
breaks and the extension is unavailable, this is how a private league still
gets connected the same day.

### Desktop Chrome / Edge instructions

1. Sign in to ESPN Fantasy normally.
2. Open your ESPN Fantasy league.
3. Press **F12** (or right-click → Inspect) to open Developer Tools.
4. Open the **Application** tab.
5. In the left sidebar expand **Storage → Cookies**.
6. Select the ESPN domain that holds the values — `espn.com` or
   `fantasy.espn.com` (check both if one is missing them).
7. Find the two rows:
   - `SWID`
   - `espn_s2`
8. Copy the **Value** field of each.

`[SCREENSHOT: DevTools Application → Cookies → espn.com]`

**Getting the values right — this is where it goes wrong:**

- `SWID` looks like `{0899A4A2-0BBB-467C-9A28-CEBC5032330E}`. **Keep the curly
  braces** if ESPN includes them.
- `espn_s2` is a long encoded string. **Copy it exactly as shown.** Do **not**:
  - URL-decode it
  - Base64-decode it
  - add or remove characters
  - replace `%2B` (or any `%`-escape) with anything
  - trim or "clean up" the value

9. In the app: Connect ESPN → **Manual — Advanced** → paste both values → season
   → **Connect ESPN account**.
10. The app **tests the credentials before saving.** On success it discovers
    your leagues, identifies your team from the `SWID`, encrypts the values, and
    never shows them again.

### Security notice (show this to users)

`SWID` + `espn_s2` are effectively your ESPN session — treat them like a
password. You should:

- never post them publicly,
- never put them in GitHub, a chat, or a ticket,
- never share a screenshot that shows their values,
- use **Disconnect ESPN** in the app to delete them,
- sign out of ESPN if you want to invalidate the current session immediately.

Fantasy War Room never logs them, never returns them through any API response,
and never displays them after they are stored. Those are enforced by the code,
not just policy (`espn/redaction.py`, and no serializer emits a cookie value).

---

## Cross-cutting procedures

### Expired cookies / expired session

Symptom: a connection that worked stops, with an "ESPN denied access" or
"cookies were rejected" message.

Cause: `espn_s2` expires when you sign out of ESPN, and on its own after roughly
a year.

Fix: reconnect. Email Code (Method 1) re-establishes a session from scratch. Or
re-copy the cookies via Manual (Method 4). In the app, an auth error while
already connected shows a **Re-enter ESPN cookies / Reconnect** button that
returns you to the connect methods.

### Wrong league id

Symptom: "ESPN has no such league for that season," or a public lookup returns
nothing.

Fix: confirm the id is the number after `leagueId=` in your league's URL, and
that the **season** matches. An id from a past season won't resolve against the
current one.

### Private league returns 401

This is expected, not an outage. A private league returns HTTP 401 to any
request without valid credentials. The public-link method cannot read it. Use
Email Code or Manual, which authenticate.

### How to disconnect / reconnect

- **Disconnect:** Connect ESPN screen → **Disconnect ESPN**. Deletes the stored
  cookies, the selected league, and any pairing codes. Imported league data
  (players, draft picks) is kept — disconnecting is about credentials, not your
  history.
- **Reconnect:** run any method again. Re-entering credentials overwrites the
  old ones.

### Confirming which ESPN account / team was detected

After connecting, the connect flow shows the discovered leagues and, for each,
the team it matched to your `SWID` ("Your team: …"). For a verified (Email Code
/ Manual) connection the team is **locked to the ESPN account** and cannot be
changed on the confirm step — the server assigns it and rejects any other
`team_id`. If it shows the wrong team, the ESPN account you connected genuinely
owns that team; connect the account that owns the team you want. For a
**public** (unverified) connection there is no `SWID` to match, so the team is a
self-assertion you pick by hand and is labelled Unverified — never treated as
proof of ownership.

---

## For maintainers: the shared representation

Every method converges on exactly this, and nothing downstream branches on which
method produced it:

| Field | Source |
|---|---|
| `SWID` | the cookie / OneID GUID |
| `espn_s2` | the cookie |
| selected league id | user choice on the confirm step |
| season | user / discovery |
| verified team id | `SWID` matched to an ESPN owner id (verified methods only) |

If you add a new acquisition method, it ends by calling
`espn_connect.save_credentials(...)` and then the existing discovery/select/
import path — do **not** build a second downstream ESPN implementation.
