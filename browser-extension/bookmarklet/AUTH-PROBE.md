# ESPN auth probe — run the bearer-vs-cookie test from a phone

Answers the same question as `scratch/espn_auth_matrix.py` — *does
`lm-api-reads` accept `Authorization: Bearer`, or is it cookie-only?* — without
a terminal, without Python, and without extracting a single credential.

## Why this works on a phone when nothing else does

JavaScript running on an ESPN origin never has to **read** `espn_s2`.
`fetch(url, {credentials: 'include'})` makes the browser attach it — HttpOnly
and all — while keeping the value invisible to script.

So the cookie rows run fine on a phone even though the cookie is unreadable,
and nothing is ever copied, stored, or sent anywhere but ESPN.

## What it can and cannot settle

| Row | Phone-answerable? |
| --- | --- |
| Cookie baseline | Yes — definitive |
| No-auth control | Yes — definitive |
| Bearer variants | **Only if** a token sits in page storage **and** CORS permits the header |

Adding an `Authorization` header to a cross-origin request triggers a CORS
preflight. If ESPN refuses it, the request dies in the browser before ESPN sees
it — which is **not** the same as ESPN rejecting the auth. Those rows report
`INCONCLUSIVE`, never "rejected". A `401` is an answer; `Failed to fetch` isn't.

If it comes back inconclusive, the desktop Python script is the fallback.

## Install on iPhone (Safari)

1. Open any page → Share → **Add Bookmark** → Save.
2. **Bookmarks** (open-book icon) → **Edit** → tap the bookmark you just made.
3. Rename it `ESPN probe`. Tap the **URL** field, select all, delete.
4. Paste the contents of `auth-probe.bookmarklet.txt` — the whole thing, it
   starts with `javascript:`. Tap **Done**.

Safari strips the `javascript:` prefix if you paste into the *address* bar, so
it has to go in via the bookmark editor.

## Install on Android (Chrome)

Bookmark any page, then **⋮ → Bookmarks → edit** it and replace the URL the same
way. Run it by typing the bookmark's name in the address bar and picking it from
the suggestions.

## Run it

1. Open **fantasy.espn.com** and navigate to your league — signed in, private
   league. A public league can't test authentication and the probe says so.
2. Open the bookmarklet.
3. A panel slides up from the bottom with the table and a verdict.
4. Screenshot it.

## Privacy

Every request goes to an `espn.com` host and nowhere else — verified by test.
No `console.log`. Nothing written to storage. If a token is found, only the
storage key it came from is displayed, never the value. Close the panel and
nothing remains.

## Rebuilding the one-liner after editing the source

```bash
npx terser browser-extension/bookmarklet/auth-probe.js --compress --mangle \
  --format quote_style=1 -o /tmp/min.js
node -e "const f=require('fs');f.writeFileSync(
  'browser-extension/bookmarklet/auth-probe.bookmarklet.txt',
  'javascript:'+encodeURI(f.readFileSync('/tmp/min.js','utf8').trim())
    .replace(/#/g,'%23').replace(/&/g,'%26')+'\n')"
```

Verify the minified build still behaves before shipping it — a comment that
survives minification can silently comment out the rest of a one-line
bookmarklet, which is exactly what happened on the first attempt here.
