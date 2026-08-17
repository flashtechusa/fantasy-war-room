# Roadmap

Everything left to build, in the order it should happen. Sizes are rough
working days, not promises.

Status as of writing: the app runs on an always-on Windows VPS behind HTTPS at
`warroom.flashtechusa.com`, gated by a single shared password in Caddy. One
league, one user, no accounts.

---

## A. Alerts — the thing worth paying for

The reason a client would bookmark this. Half-built.

| | Item | Size | Notes |
|---|---|---|---|
| A1 | Detection engine | done | Lineup breaks, start/sit, waivers, injuries — with thresholds so it doesn't cry wolf |
| A2 | Schedule logic | done | Derived from each league's `waiverProcessDays`; missed windows still fire |
| A3 | Background runner | 0.5 | Loop in the app lifespan that checks due windows and runs detection |
| A4 | Alert storage | 0.5 | Persist sent keys so dedupe survives restarts; alert history |
| A5 | Web push delivery | 1.5 | VAPID keys, service worker, PWA manifest, subscription endpoints |
| A6 | Alert settings UI | 0.5 | Which alerts, how early, quiet hours |
| A7 | Alert history screen | 0.5 | What fired and when — also how you debug a missed alert |

**A5 is the risky one.** iOS only allows web push for apps added to the home
screen, and permission prompts behave differently there. Needs testing on a
real iPhone, not an emulator.

---

## B. Accounts — the gate on everyone else using it

Nothing here exists. This is the biggest block of work and it is what stands
between "my tool" and "a thing with customers."

| | Item | Size | Notes |
|---|---|---|---|
| B1 | User model + roles | 0.5 | owner / partner / client |
| B2 | Password hashing | 0.25 | `hashlib.scrypt` — stdlib, no new dependency |
| B3 | Sessions | 0.5 | Signed HttpOnly cookie + server-side session table so accounts can be revoked instantly |
| B4 | Login screen | 0.5 | Replaces the browser's basic-auth box, which cannot be branded or explained |
| B5 | Admin: create user | 0.5 | Invite-only. No public signup route exists at all |
| B6 | Invite flow | 0.5 | One-time expiring link; the client sets their own password |
| B7 | Season entitlement | 0.5 | `(user, season, enabled)`; paywall screens, keep the data |
| B8 | Partner console | 0.5 | The drafter flips accounts on and off; cannot see rosters |
| B9 | Per-user ESPN credentials | 1 | **Encrypted at rest.** Hard prerequisite before anyone else's cookies land here |
| B10 | Multi-tenant scoping | 1.5 | Thread the current user through `get_active_league` and the board cache |
| B11 | Retire Caddy basic_auth | 0.1 | Only once B1–B4 work |
| B12 | Login rate limiting | 0.25 | Lockout after repeated failures |

**B10 is the sleeper.** The valuation engine is cached per league; with many
users that cache has to be keyed correctly or people see each other's boards.

---

## C. Matchups and standings

Requested. Needs a new import path.

| | Item | Size | Notes |
|---|---|---|---|
| C1 | Import schedule + scores | 0.5 | `espn-api` exposes `team.schedule` and `scoreboard()` |
| C2 | Matchup screen | 1 | Your team vs this week's opponent, projected and live |
| C3 | Standings | 0.5 | Record, points for/against, streak |
| C4 | Playoff picture | 0.5 | Seeding and elimination maths from `playoff_team_count` |

---

## D. Model improvements

Not urgent, but this is where the product gets genuinely hard to copy.

| | Item | Size | Notes |
|---|---|---|---|
| D1 | Store position on historical picks | 0.25 | Needed before any owner modelling is possible |
| D2 | Owner draft tendencies | 1.5 | Who reaches for QB, who punts TE — blended against ADP by how much history exists |
| D3 | Projection calibration | 1 | Compare our weekly projections against actual results; report the error honestly |
| D4 | A second projection source | 1 | The schema already supports it; removes single-provider dependence |

---

## E. Operations

Unglamorous, and the reason things break at 11am on a Sunday.

| | Item | Size | Notes |
|---|---|---|---|
| E1 | Database backups | 0.25 | Nightly copy of the SQLite file; currently one disk failure from total loss |
| E2 | ESPN credential expiry | 0.5 | `espn_s2` expires. Detect it and prompt, rather than failing silently |
| E3 | Uptime monitoring | 0.25 | Something external that notices the app is down |
| E4 | Windows update path | 0.5 | The in-app Update button shells out to git, which the VPS does not have |

---

## F. Known debt

- **Live draft sync does not work.** The one capability that failed in real
  use. Required if the free draft-watching tier is ever going to exist.
- **No CI.** `pytest` and `npm run typecheck` on push would be cheap.
- **Docker image never built.** No daemon was available where this was written.
- **No end-to-end tests.** The UI has only ever been checked by hand.
- **Alert delivery untested against a real iPhone.**

---

## Suggested order

1. **A3–A5** — finish alerts and get one firing on your phone. Nothing else
   matters until the product does something a client would pay for.
2. **E1** — backups. One disk failure currently loses everything.
3. **C1–C2** — matchups. Visible, self-contained, and makes the app feel whole.
4. **B1–B8** — accounts and the login screen, once there is a real person to
   give one to.
5. **B9–B10** — credential encryption and tenant scoping. **Blocking** for any
   non-owner user; do not skip to save time.
6. **D1–D2** — owner tendencies, before next year's draft.

Billing does not appear anywhere because payment happens on Fiverr. The only
money-adjacent work is B7 and B8, the on/off switch.
