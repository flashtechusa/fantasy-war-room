# Fantasy War Room ESPN Connector (browser extension)

A Manifest V3 proof-of-concept that removes the manual cookie-copying step from
connecting an ESPN account.

It exists because of one hard fact: **`espn_s2` is an `HttpOnly` cookie.**
Page JavaScript cannot read it, so no bookmarklet, injected script or helper
page can either. The `chrome.cookies` API is the only interface a browser
offers that can, and it is gated behind an explicit extension permission. That
makes an extension the only clean way to automate this in a browser.

## What it does

1. You open your ESPN league in Chrome or Edge and click the extension.
2. It checks whether ESPN's two session cookies exist — it reports *found* or
   *not found*, never the values.
3. It reads the league id out of the page URL (not a secret).
4. You paste a **pairing code** generated in Fantasy War Room.
5. On click, it reads the two cookies, POSTs them once to
   `POST /api/espn/extension/connect` on **your own** Fantasy War Room server,
   and discards them.

## Permissions, and why each one is there

| Permission | Why | Scope |
| --- | --- | --- |
| `cookies` | The only way to read an `HttpOnly` cookie | Paired with the host permission below, so it reaches ESPN only |
| `host_permissions: https://fantasy.espn.com/*` | Bounds `cookies` to ESPN fantasy | One host. Not `*://*/*`, not `*.espn.com` |
| `activeTab` | Read the league id from the tab you are looking at | Only after you click the extension; no persistent tab access |
| `storage` | Remember your server address between uses | Stores the URL only — never a code, never a cookie |
| `optional_host_permissions` | POST to your own server | Requested at connect time for the exact origin you typed, then only that origin |

There is no `<all_urls>`, no content script, no background service worker, and
no request to any host other than ESPN's cookie store and your own server.

## Security properties

- **Cookie values never enter the UI.** The popup renders "found" / "not
  found". There is no element that could contain a credential.
- **Nothing is persisted.** Cookies live in a local variable for the duration
  of one click. `chrome.storage.local` holds only the server URL.
- **Nothing is logged.** No `console.log` of a cookie exists in this codebase;
  the error path deliberately reports a generic message rather than echoing a
  request.
- **Transport is restricted.** The Connect button refuses anything that is not
  `https:`, except `http://localhost` / `http://127.0.0.1`, so a credential is
  never sent in clear text over a network.
- **Auth is a single-use code.** The extension holds no long-lived token for
  Fantasy War Room. The pairing code is issued to an already-signed-in user,
  expires in five minutes, works once, and is revoked when that user
  disconnects ESPN.
- **`credentials: 'omit'`** on the POST, so no ambient cookies ride along.

## Install (unpacked, for testing)

1. `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select this `browser-extension/` directory.
3. In Fantasy War Room: **Connect ESPN → Use the browser extension → Generate
   pairing code**.
4. Open your ESPN league, click the extension, enter your server address and
   the code, then **Connect this ESPN account**.

This is a proof-of-concept, not a published extension. It has not been through
Chrome Web Store review; loading it unpacked is the intended way to use it.

## Payload contract

The backend publishes the contract at
`GET /api/espn/extension/manifest-contract`, and the test suite asserts the
extension and the backend agree:

```json
{
  "pairing_code": "ABCD2345",
  "swid": "{...}",
  "espn_s2": "...",
  "league_id": 123456,
  "season": 2026,
  "client": "extension 0.1.0"
}
```

Unknown fields are rejected by the server (`extra: forbid`), so a future
extension version cannot quietly start sending more than the backend expects.

## Option B: the bookmarklet

`bookmarklet/` contains a bookmarklet that extracts the league id, season and
team id from an ESPN page URL, and reports which cookies are readable from page
JavaScript. It **cannot** read `espn_s2`, and it says so rather than pretending
otherwise. See `bookmarklet/README.md`.

## Option C: a desktop cookie-reading helper

Investigated and deliberately **not** shipped. See
[`../docs/espn-connection.md`](../docs/espn-connection.md#option-c--desktop-helper)
for the analysis: reading Chrome's cookie database requires decrypting it with
an OS keychain secret that also unlocks every other cookie the browser holds,
which is a much larger blast radius than the extension for the same result.
