# Product direction

Decisions made in conversation, written down so they survive it. This is the
intended shape of the product, not a description of what is built — see
`TODO.md` for the gap.

Nothing here is built yet. The app today is single-tenant and read-only
against ESPN.

---

## What it is

A season-management add-on sold alongside a professional drafting service.

The drafter (currently found and paid through Fiverr) drafts a client's team.
That is his business and it stays his business. This tool covers the seventeen
weeks afterwards, which he is not paid for and does not do. It is positioned
as the middle tier: cheaper than paying him to manage a full season, for people
who want his draft but cannot justify the rest.

## What it does NOT do: write to ESPN

**The app never makes transactions.** It reads the league, works out what it
would do, and notifies the user. They execute it themselves in ESPN.

This is a deliberate constraint, not a missing feature:

- `espn-api` is read-only. Writes would mean hand-rolling calls to ESPN's
  private, undocumented transaction endpoint.
- Drops and FAAB bids are irreversible. A bug spends real money or discards a
  real player.
- "We hold your credentials and act on your behalf" is a materially different
  liability than "we read your league and alert you".

Worst case for a wrong alert is the user ignores it. That asymmetry is the
whole reason for the design.

## Accounts and access

Payment happens on Fiverr, outside the app entirely. No Stripe, no card data,
no billing code. The app answers one question: *is this account on for this
season?*

Access is per **season**, which the schema already models — `League` is keyed
on `(espn_league_id, season)`. An entitlement is `(user_id, season, enabled)`.
When the season ends nothing has to expire: the next season simply has no row,
so it is off until someone turns it on.

Three roles:

| Role | Can do |
|---|---|
| Owner | Everything; full visibility across all accounts |
| Partner (the drafter) | Enable/disable accounts for a season |
| Client | Their own team only |

When an account is off, paywall the screens but **keep the data**. Their league
history is the reason they come back, and forcing a re-import is the friction
that stops them.

## Credentials

Each client connects their **own** ESPN account. The app is sold to clients,
not operated on their behalf — the drafter is the distribution channel.

This matters. The alternative considered was routing everything through the
drafter's credentials, which fails because he does not use ESPN's co-manager
feature; he logs in as the client. Building a product on shared logins would
mean storing many people's account credentials for a workflow that violates
ESPN's terms on account sharing. Each user authorising their own account is
both safer and unremarkable.

Read-only still needs `espn_s2` for private leagues — ESPN has no read-only
token. So credentials must be **encrypted at rest** before anyone other than
the owner uses this. That is a hard prerequisite, not a nice-to-have.

## The alerts

Timed off each league's real settings, which are already imported
(`waiver_process_days`, scoring, roster slots) rather than a generic schedule.

| Alert | When | Why it earns a notification |
|---|---|---|
| Waiver targets | Night before the league processes waivers | Ranked adds with a suggested FAAB bid, under this league's scoring |
| Broken lineup | ~90 min before kickoff | A starter is OUT, on bye, or inactive — the most costly unforced error, and entirely preventable |
| Better start available | Sunday morning | Only when the margin clears the noise floor, so it does not cry wolf |
| Injury changes the week | As news lands | A starter's status changed since they last looked |

Delivery is **web push** to a home-screen PWA: works on iOS, no App Store, no
Apple developer account, no SMS bill. Email as the fallback.

## Known risks

- **ESPN's terms.** This runs on a private, undocumented API. Personal use is
  one thing; charging for access is a different posture and they can cut it off.
- **Single point of failure.** If ESPN changes that API mid-season, every
  customer breaks the same morning and the support call is ours.
- **The free draft tier does not exist.** Letting a client watch their draft in
  real time is the one capability that failed in live use. If it is the hook,
  it has to be built and proven first.

## Order of work

1. Running reliably on always-on hosting (in progress — Windows VPS)
2. Alerts working for a single user, proven over a few weeks
3. Accounts, roles, the on/off switch, credential encryption
4. Anything sold to anyone

Billing-shaped work is last on purpose. It is worthless until there is
something to sell and somewhere to sell it from.
