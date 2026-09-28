# Mastodon live connector (Phase 13)

The first real connector. Every endpoint it uses is verified against the official
documentation (docs/PLATFORM_CAPABILITIES.md §4.9, checked 2026-09-27). Publication stays
**simulated** until every step below is done by a person. OmniGrowth never posts on its own
initiative, and nothing here is automated by the agent that built it.

## What it does

- Publishes approved **text posts** and **threads** (a thread is a chain of replies) to one
  account on one Mastodon server. Media are not uploaded: suggested media are listed in the
  preview as "not attached".
- Sends each part with an `Idempotency-Key` derived from the content version and account.
  The server keeps these for one hour, so a request that is repeated after a connection
  failure does not create a second post.
- Treats anything it cannot prove as **uncertain**: a timeout after sending, a server error,
  an unreadable answer, or a thread that stopped part-way. An uncertain post is **never
  retried automatically**. The account publishes nothing else until a person reconciles it.
- **Reconciles by lookup**: "Look up on the platform" searches the account's recent posts
  for the exact text through the read-only API. It never posts again to find out. If it
  finds the post, the post is marked published. If it finds nothing, nothing changes,
  because absence is not proof.
- Reads **metrics**: favourites, boosts and replies. Mastodon has no impressions, reach or
  views, so those stay empty rather than being set to zero.
- Handles **rate limits**: a 429 answer is retried after `X-RateLimit-Reset`. A request that
  never left (connection refused, DNS failure) is retried with backoff.

## Evidence classes

Every publication records what it can prove, fixed at the moment it is sent:

| Class | When | Counts as real evidence? |
|---|---|---|
| SIMULATED | Mock publisher (`PUBLISH_MODE=MOCK`) | No |
| SANDBOX | Live post from an account marked **sandbox** (a test account or test server) | No: a real API call, but no real audience |
| REAL | Live post from a real account | Yes (with API or manual metrics) |

Mark a test account as sandbox **before** switching it to live. The flag cannot change while
live publishing is on.

## Setup (a person does all of this)

1. **Server key for credentials.** In the server's `.env` (never committed), set
   `OMNIGROWTH_CREDENTIAL_KEK` to a random 32-byte key, base64url-encoded, for example the
   output of `python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.
   Losing or changing this key makes every stored token unreadable (enter them again).
2. **Account.** Platforms → Add an account → Mastodon, with the handle, the **instance
   address** (only the server, for example `https://mastodon.social`) and who sees live posts
   (public / unlisted / followers only).
3. **Token.** Create an application for the account with only these scopes:
   `write:statuses read:statuses read:accounts`. The verified way is the OAuth flow
   (register an app with `POST /api/v1/apps`, authorize with `GET /oauth/authorize`,
   exchange the code at `POST /oauth/token`; PKCE since 4.3.0; see
   https://docs.joinmastodon.org/client/authorized/). Many servers also offer
   *Preferences → Development → New application*, but that path is not described in the
   official docs (UNVERIFIED). Check the server's rules on automated posting and consider
   setting the account's bot flag.
4. **Store it.** Platforms → Manage connection → paste the token → Store token. It is
   encrypted (AES-256-GCM, envelope) and never shown again. Only a fingerprint is displayed.
   Storing a new token revokes the old one. Revoking destroys the encrypted token and
   switches live off.
5. **Self-hosted or test server** reachable over HTTP or on a private address: add it to
   `OMNIGROWTH_PLATFORM_HOST_ALLOWLIST` as `host:port`. Public servers must be HTTPS on
   port 443.

## The first live post (the owner decides every step)

1. Approve the exact content version yourself. Auto-approval never satisfies a live post.
2. Schedule it to the account. On the Publishing page, **Preview** shows exactly what would
   be sent: account and server, every post in the thread, visibility, media (none
   attached), timing, evidence class, and anything that would block it. Review it.
3. Type `ENABLE LIVE PUBLISHING FOR MASTODON` in the account's connection dialog. This is
   refused when the server runs with `ENV=test`.
4. Restart the scheduler with `PUBLISH_MODE=LIVE` (`ENV` must not be `test`). In this mode,
   platforms without a verified connector are refused, not simulated.
5. At the due time the dispatcher checks everything again before sending:
   - brand match
   - `ENV` is not `test`
   - the account is live-enabled
   - the approved version is the one scheduled
   - a person approved it
   - the connector and a stored credential exist
   - no uncertain post is open on the account
6. Record the result (post id, link, any reconciliation) in the engineering log. Then pull
   metrics once and compare which fields came back with §4.9.

## Known limitations

- Text only: no media upload, no polls, no content warnings, no delete action.
- Thread posting stops at the first failure. The post is then uncertain, and a person
  decides after a lookup.
- Instance limits (characters, URL length) come from the account settings. They are not
  fetched from the server.
- There is no "verify token" button yet. The first check of a token is the first live
  dispatch or lookup.
- Tested end to end against a local fake server (`tests/integration/fake_mastodon.py`).
  It has not been run against a real Mastodon server.
