# PLATFORM_CAPABILITIES.md — platform API capability register

> **Document status:** Phase 1 draft (2026-09-22, no documentation consulted). **Phase 13 update (2026-09-27):** `MASTODON` verified against the official documentation at docs.joinmastodon.org (§4.9, every claim with URL + date); cost / access facts recorded for `X` and `LINKEDIN` (§4.1, §4.2) to justify the choice of the first connector. Everything not explicitly marked VERIFIED below is still **UNVERIFIED**.
>
> **Governing rules:** AGENTS.md §3 (no network calls to social platforms; no `PUBLISH_MODE=live` without the exact human phrase), AGENTS.md §6 (unknown capability → UNVERIFIED; never implement against guessed endpoints), SPEC_AMENDMENTS A6 (publishing safety, `ManualExportPublisher`), A7 (MVP scope), PHASES.md Phase 13 (real connectors, one platform per session). Names follow `docs/GLOSSARY.md`.

---

## 1. Status values and the implementation rule

Each capability cell (publish, schedule, delete, metrics, media upload, auth, rate limits) carries a `PlatformCapability` status:

| Status | Meaning | Evidence required |
|---|---|---|
| `VERIFIED` | Capability confirmed from the platform's **official** developer documentation (or official terms / developer policy page). | Official doc URL **and** the date it was checked (`YYYY-MM-DD`), recorded next to the claim. Third-party blogs, SDK READMEs, forum posts, and model recall do **not** count. |
| `UNVERIFIED` | Unknown, assumed, remembered, or reported secondhand. | None — this is the default. |
| `UNAVAILABLE` | Official documentation (or official statement) confirms the capability does not exist, or is not available to our account type / tier. | Same as `VERIFIED`: official URL + date checked. |

**Rule (binding, from AGENTS.md §6):** No `PlatformPublisher` adapter method (spec §15: `validate`, `publish`, `schedule`, `delete`, `fetch_metrics`) may be implemented against a real platform endpoint while the corresponding capability is `UNVERIFIED`. Until then that platform uses `MockPublisher` (spec §34, `PublishMode.MOCK`) and, where a real publish path is later confirmed `UNAVAILABLE`, `ManualExportPublisher` (`PublishMode.MANUAL`, A6).

**Wording rule for this file:** claims are phrased as "to verify: …". Anything commonly reported but not checked is tagged **[hearsay — unverified]** and must never be treated as a requirement or design input until verified.

**In this version only `MASTODON` is `VERIFIED` (publish, auth, rate limits); every other row stays `UNVERIFIED`.** Official documentation was read with the human's authorization of 2026-09-27 ("current official API documentation"); no platform API was called.

---

## 2. Summary table

Legend: `U` = `UNVERIFIED`, `V` = `VERIFIED` (see the platform section for URL + date). "MVP publisher" is what runs in the MVP (Phase 7) and until Phase 13 verifies that platform. "Planned fallback" applies if the publish capability is later found `UNAVAILABLE` or unusable for our account type. Phase = writer phase / connector phase (PHASES.md).

| Platform | Publish API | Schedule API | Delete API | Metrics API | Media upload | Auth model | Rate limits | Status | MVP publisher | Planned fallback | Phase (writer / connector) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `LINKEDIN` | U | U | U | U | U | U | U | `UNVERIFIED` | `MockPublisher` | `ManualExportPublisher` if personal/page publish unavailable | 6 / 13 |
| `X` | U | U | U | U | U | U | U | `UNVERIFIED` | `MockPublisher` | `ManualExportPublisher` if API tier is unaffordable/unavailable | 6 / 13 |
| `INSTAGRAM` | U | U | U | U | U | U | U | `UNVERIFIED` | `MockPublisher` | `ManualExportPublisher` (likely needed for `STORY` — to verify) | 10 / 13 |
| `FACEBOOK` | U | U | U | U | U | U | U | `UNVERIFIED` | `MockPublisher` | `ManualExportPublisher` for personal profiles (to verify) | 10 / 13 |
| `YOUTUBE` | U | U | U | U | U | U | U | `UNVERIFIED` | `MockPublisher` | `ManualExportPublisher` (upload checklist) | 10 / 13 |
| `REDDIT` | U | U | U | U | U | U | U | `UNVERIFIED` | `MockPublisher` | `ManualExportPublisher` | 10 / 13 |
| `SUBSTACK` | U | U | U | U | U | U | U | `UNVERIFIED` | `MockPublisher` | `ManualExportPublisher` (expected primary path — to verify) | 10 / 13 |
| `RUMBLE` | U | U | U | U | U | U | U | `UNVERIFIED` | `MockPublisher` | `ManualExportPublisher` (expected primary path — to verify) | 10 / 13 |
| `MASTODON` | **V** | V (not used) | V | V (partial) | U (not implemented) | **V** | **V** | **`VERIFIED`** | `MockPublisher`; `MastodonLivePublisher` behind A6 | `ManualExportPublisher` per instance if needed | 10 / 13 (connector built) |

Notes:
- Per A7, only `LINKEDIN` and `X` writers were in MVP scope. Since Phase 10 (A15) every platform has a writer; publishing for all of them is still `MockPublisher` and every capability here stays `UNVERIFIED`.
- Spec §15's required-connector list omits Mastodon; the `Platform` enum and §7E include it. Treated here as in scope for Phase 13 (see Open questions).
- Every `fetch_metrics` result must leave unobtainable `AnalyticsSnapshot` fields `NULL` (spec §6: "Use nullable metrics"); never zero-fill (spec principle 13, PHASES Phase 8 gate).

---

## 3. Common questions (apply to every platform)

Answer each from official docs during the Phase 13 session for that platform. Record URL + date beside each answer.

1. **Auth / app review:** What auth flow (OAuth 2.0 variant, PKCE, app tokens, instance-issued tokens)? Is developer-app registration required? Is app review / partner approval required before posting on behalf of a user? How long do access tokens and refresh tokens live?
2. **Scopes / permissions:** Exact scopes needed for publish, delete, read metrics, upload media.
3. **Account types:** Is publishing available for personal accounts, organization/page/brand accounts, or both? Which `PlatformAccountType` values does each capability apply to?
4. **Publish endpoint:** Which `ContentFormat`s can be published via API vs. only through the native app?
5. **Scheduling:** Native scheduled publish via API, or must our `scheduler` (A3 outbox) publish at the due time? (Default design assumption: our outbox publishes at due time; native scheduling is optional.)
6. **Deletion:** Can a published item be deleted via API? Edited? (Governs `publication_status = DELETED` and the `delete` adapter method.)
7. **Reconciliation:** Can we look up a post by our own reference or list recent posts, so a `PUBLICATION_UNCERTAIN` job can be reconciled (A3) without re-posting?
8. **Metrics:** Which `AnalyticsSnapshot` fields are obtainable, at what granularity, with what delay, for how long after posting, and for which account types?
9. **Media constraints:** Allowed types, max size, dimensions/aspect ratios, video duration, count per post, upload flow (single vs. chunked/resumable).
10. **Text limits:** Character/length limits per format; link, hashtag, and mention handling; markdown/formatting support.
11. **Rate limits / quotas:** Per-user, per-app, per-endpoint; daily quotas; response headers to honor (`PLATFORM_RATE_LIMIT` error mapping).
12. **Terms of service / automation policy:** Is automated or scheduled posting permitted? Any disclosure requirements for AI-generated content? Restrictions on storing metrics? Developer-policy limits on data retention.
13. **Cost / tier:** Is API access free, tiered, or paid? What does our intended volume require?

---

## 4. Per-platform sections

`AnalyticsSnapshot` fields referenced below (spec §6, names per DATA_MODEL): `impressions, views, reach, likes, comments, shares, saves, clicks, profile_visits, followers_gained, watch_time_seconds, average_view_duration_seconds, ctr, conversion_count, revenue`. Platform-specific metrics with no dedicated field go in `analytics_snapshots.platform_metrics` (jsonb). Every snapshot carries a `MetricSource` (`MOCK, FIXTURE, API, MANUAL`).

Account-type-dependent capabilities below refer to `platform_accounts.account_type` (`PlatformAccountType`: `PERSONAL, ORGANIZATION, PAGE, CHANNEL, SUBREDDIT_POSTER, INSTANCE_USER`). `conversion_count` and `revenue` are expected to come from our own attribution (UTM/landing-page/n8n) rather than any platform API for all platforms — to verify per platform whether any native equivalent exists.

### 4.1 `LINKEDIN`

**Formats we intend to support (§8):** `TEXT_POST` (authority, storytelling, analytical, short text), `DOCUMENT_CAROUSEL`, `POLL` (only when justified), `ARTICLE`, `NEWSLETTER` (newsletter/article adaptations).

**Questions to answer from official docs:**
- To verify: whether posting on behalf of a **personal member** vs. an **organization page** use different products/permissions, and which require partner/app review. [hearsay — unverified: commonly reported that some LinkedIn API products require approval and that organization-page APIs differ from member posting.]
- To verify: whether document (PDF) posts, polls, articles, and newsletters are publishable via API at all, or only in the native UI.
- To verify: native scheduling support; delete and edit support.
- To verify: which metrics are available for member posts vs. organization posts. [hearsay — unverified: analytics are commonly reported to be more available for organization pages than for personal posts.]
- To verify: text limits, media constraints (image, video, document), rate limits, token lifetime, automation terms.

**Metrics we want (§8 track-list → `AnalyticsSnapshot`):**

| §8 LinkedIn metric | Field |
|---|---|
| impressions | `impressions` |
| dwell proxies | `platform_metrics` (jsonb; key name to define at Phase 13) |
| reactions | `likes` |
| comments | `comments` |
| reposts | `shares` |
| saves if available | `saves` |
| profile views | `profile_visits` |
| follows | `followers_gained` |
| clicks | `clicks` (and `ctr` if provided or derivable) |
| also wanted | `reach`, `views` (video), `conversion_count`, `revenue` (own attribution) |

**Fallback:** `ManualExportPublisher` (copy + document/media checklist; human marks published and pastes URL) + manual metrics entry. Likely needed for any format found unpublishable via API.

**Verified 2026-09-27 (access only):** member posting needs the `w_member_social` permission from the "Share on LinkedIn" product added to a developer app; the documented endpoint is `POST /v2/ugcPosts`; throttles 150 requests/day per member and 100,000/day per application (https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin, page last updated 2023-12-14, checked 2026-09-27). Whether that endpoint is still the current one is UNVERIFIED; publishing stays UNVERIFIED.

### 4.2 `X`

**Formats (§8):** `TEXT_POST` (standalone; long-form posts "when supported"), `THREAD`, `COMMENT_DRAFT` (replies), `QUOTE_POST` (quote-post drafts).

**Questions:**
- To verify: API access tiers, what each tier permits (write, read, metrics), and monthly cost. [hearsay — unverified: commonly reported that X API access is tiered and that meaningful write/read access is paid, with low free-tier limits.]
- To verify: thread posting (reply-chaining) semantics and partial-failure behavior mid-thread (critical for `PUBLICATION_UNCERTAIN` handling: a thread may be half-posted).
- To verify: long-form post availability via API and whether it depends on the account's subscription status.
- To verify: quote-post creation via API; native scheduling (assume none until verified); delete support.
- To verify: which post metrics are exposed (impressions, likes, reposts, replies, bookmarks, link clicks, profile clicks) and at what tier.
- To verify: character limits, media constraints, rate limits, automation rules (e.g., rules on automated replies/mentions).

**Metrics we want:** `impressions`, `likes`, `comments` (replies), `shares` (reposts + quotes — decide whether combined), `saves` (bookmarks), `clicks`, `profile_visits`, `followers_gained`, `views` (video), `ctr`, `conversion_count`, `revenue` (own attribution).

**Fallback:** `ManualExportPublisher` (thread rendered as numbered segments) + manual metrics entry. Tier cost may make this the practical default — a human decision (see Open questions).

**Verified 2026-09-27 (cost only):** API access is pay-per-use with no free tier; creating a post costs $0.015 per request and a post with a URL $0.200 (https://docs.x.com/x-api/getting-started/pricing, checked 2026-09-27). Every other X capability stays UNVERIFIED.

### 4.3 `INSTAGRAM`

**Formats (§8):** `REEL_SCRIPT`, `CAROUSEL`, `STORY`, `IMAGE_POST` (captions are part of each asset; hook, visual direction, scene breakdown, caption, CTA per §8).

**Important:** OmniGrowth produces *scripts and copy*, not rendered media. A real publish requires finished media files the human supplies. To decide: whether the adapter is ever "publish" or always "publish with human-attached media".

**Questions:**
- To verify: requirement for a professional (business/creator) account and a linked Facebook page. [hearsay — unverified: commonly reported that Instagram API publishing requires a professional account and Meta app review.]
- To verify: which formats are publishable by API (feed image, carousel, reel, story). [hearsay — unverified: Story publishing via API is commonly reported as restricted.]
- To verify: scheduling support, delete support (possibly none — to verify), media hosting requirements (e.g., whether media must be at a public URL rather than uploaded directly — would conflict with our local-first design).
- To verify: insight metrics per media type and account-type restrictions; daily publish limits; caption/hashtag limits.

**Metrics we want:** `impressions`, `reach`, `views` (reels), `likes`, `comments`, `shares`, `saves`, `profile_visits`, `followers_gained`, `watch_time_seconds`, `average_view_duration_seconds`, `clicks`, `ctr`, `conversion_count`, `revenue` (own attribution).

**Fallback:** `ManualExportPublisher` (caption + media checklist + scene breakdown) + manual metrics entry.

### 4.4 `FACEBOOK`

**Formats:** §8 does not define Facebook formats. **Proposed (to confirm with human):** `TEXT_POST`, `IMAGE_POST`, `REEL_SCRIPT`, `STORY`, `LONG_VIDEO_OUTLINE`.

**Questions:**
- To verify: page vs. personal-profile publishing. [hearsay — unverified: commonly reported that API publishing is available for Pages but not personal profiles.]
- To verify: Meta app review requirements and permissions; whether Facebook and Instagram share one app/credential (spec §15 groups them as "Instagram/Facebook").
- To verify: native scheduled posts for pages, delete/edit, page and post insights availability, media constraints, rate limits, automation terms.

**Metrics we want:** `impressions`, `reach`, `views`, `likes` (reactions), `comments`, `shares`, `clicks`, `ctr`, `followers_gained`, `watch_time_seconds`, `average_view_duration_seconds`, `conversion_count`, `revenue` (own attribution).

**Fallback:** `ManualExportPublisher` (required for personal profiles if confirmed) + manual metrics entry.

### 4.5 `YOUTUBE` (includes Shorts = `YOUTUBE` + `SHORT`)

**Formats (§8):** `LONG_VIDEO_OUTLINE`, `LONG_VIDEO_SCRIPT`, `SHORT`, `COMMENT_DRAFT` (pinned-comment suggestions). Title candidates, thumbnail concepts, description, and chapters are asset fields, not formats.

**Note:** as with Instagram, we produce scripts and metadata; the human produces the video. "Publish" in practice means "upload human-supplied video with our metadata" — to decide.

**Questions:**
- To verify: upload API availability, quota-unit costs per upload, and daily quota. [hearsay — unverified: commonly reported that uploads consume a large share of the default daily quota, and that uploads from unaudited API projects may be forced private until an audit.]
- To verify: scheduled publish (e.g., upload as private with a publish time), delete, metadata update (title/description/chapters after publish), custom thumbnail upload requirements (possibly account verification).
- To verify: how Shorts are identified via API (no distinct endpoint vs. flag).
- To verify: which metrics come from the data API vs. a separate analytics/reporting API, and their delay.
- To verify: pinned-comment support via API (pinning may be UI-only — to verify).

**Metrics we want:** `views`, `impressions`, `ctr` (thumbnail CTR), `watch_time_seconds`, `average_view_duration_seconds`, `likes`, `comments`, `shares`, `followers_gained` (subscribers gained), `clicks`, `conversion_count`, `revenue` (possibly available via monetization analytics — to verify; otherwise own attribution).

**Fallback:** `ManualExportPublisher` (title/description/chapters/tags/thumbnail-brief package + upload checklist) + manual metrics entry.

### 4.6 `REDDIT`

**Formats:** `DISCUSSION` (community-native text post), `TEXT_POST`, `COMMENT_DRAFT`. §8: must be community-native; avoid obvious promotional language.

**Per-subreddit configuration (§8) — fields to capture. Storage: validated JSON in `platform_accounts.settings` (one entry per subreddit) for now; field names below are proposed:**

| Field | Purpose |
|---|---|
| `subreddit` | Name (without `r/`). |
| `rules_text` / `rules_summary` | Captured subreddit rules (human-maintained snapshot, with `rules_checked_at`). |
| `self_promotion_policy` | Allowed / restricted / banned, plus the subreddit's stated ratio if any. |
| `self_promotion_ratio` | Target max ratio of promotional to non-promotional activity for our account (e.g., a configurable fraction — value supplied by human, never assumed). |
| `link_posts_allowed`, `text_posts_allowed` | Post-type restrictions. |
| `flair_required`, `allowed_flairs` | Required post flair and valid choices. |
| `title_rules` | Required prefixes/tags, length rules. |
| `min_account_age_days`, `min_karma` | Posting eligibility thresholds if the subreddit states them. |
| `posting_windows` / `max_posts_per_period` | Our own cadence limit to avoid spam signals. |
| `disclosure_required` | Whether affiliation must be disclosed. |
| `banned_phrases` | Subreddit-specific phrases feeding QA stage `PLATFORM`. |
| `last_reviewed_at`, `reviewed_by` | Staleness tracking; QA should warn when stale. |

**Questions:**
- To verify: API terms, app registration, OAuth scopes (submit, edit, delete, read), and whether commercial use requires a paid agreement. [hearsay — unverified: commonly reported that Reddit changed API terms and pricing in 2023, with limits on free usage and commercial use.]
- To verify: whether subreddit rules and flair lists are readable via API (would allow automated refresh of the config above).
- To verify: scheduling (assume none), delete/edit, metrics (score/upvote ratio, comment count; impressions/views likely owner-only or unavailable — to verify), rate limits, rules on automated posting and bots.

**Metrics we want:** `likes` (score/upvotes — define mapping), `comments`, `views`, `impressions`, `shares`, `clicks`, `conversion_count`, `revenue` (own attribution). Many are expected to be `NULL`.

**Fallback:** `ManualExportPublisher` (title + body + flair + subreddit checklist incl. rule reminders) + manual metrics entry. Given community norms, manual posting may be the preferred default even if the API is available — human decision.

### 4.7 `SUBSTACK`

**Formats (§8):** `NEWSLETTER`, `ARTICLE` (essay), `NOTE`. CTA and lead-magnet integration are asset fields.

**Questions:**
- To verify: whether any official public API exists for publishing posts or Notes. [hearsay — unverified: commonly reported that Substack has no official public publishing API.]
- To verify: any official import/export mechanism (e.g., RSS, email-to-post, import tools) usable for semi-automated flow.
- To verify: whether any subscriber/open/click statistics are exportable officially (e.g., CSV export from dashboard).
- To verify: terms on automation.

**Metrics we want:** `views`, `likes`, `comments`, `shares`, `clicks`, `ctr` (email click rate), `followers_gained` (new subscribers), `conversion_count` (paid conversions), `revenue`. Email open rate goes in `platform_metrics` (jsonb).

**Fallback:** `ManualExportPublisher` expected to be the primary path (markdown/HTML export + checklist) + manual metrics entry or official CSV import if one exists.

### 4.8 `RUMBLE`

**Formats:** §8 does not define Rumble formats. **Proposed:** `LONG_VIDEO_OUTLINE`, `LONG_VIDEO_SCRIPT`, `SHORT`.

**Questions:**
- To verify: whether an official, documented upload/publish API exists and how access is granted. [hearsay — unverified: a public self-serve upload API is not widely known; availability unclear.]
- To verify: official metrics access, scheduling, deletion, media constraints, terms on automation.

**Metrics we want:** `views`, `likes`, `comments`, `shares`, `watch_time_seconds`, `average_view_duration_seconds`, `followers_gained`, `revenue`, `conversion_count`.

**Fallback:** `ManualExportPublisher` expected to be the primary path + manual metrics entry.

### 4.9 `MASTODON` (Fediverse)

**Formats (proposed; §7E names the writer but §8 gives no list):** `TEXT_POST`, `THREAD`, `IMAGE_POST`, `POLL`, `COMMENT_DRAFT`.

**Per-instance differences — fields to capture. Storage: validated JSON in `platform_accounts.settings` for now; field names below are proposed:**

| Field | Purpose |
|---|---|
| `instance_base_url` | Each account lives on a specific server; all API calls are per-instance. |
| `instance_software` / `instance_version` | Fediverse servers vary (Mastodon versions and forks); feature support differs. |
| `max_characters` | Post length limit — to verify per instance (instance setting `configuration.statuses.max_characters`; VERIFIED field, see the table below). |
| `max_media_attachments`, `media_size_limits`, `supported_mime_types` | Per-instance media config. |
| `poll_limits` | Max options, option length, duration bounds. |
| `rate_limits` | Per-instance; may differ from defaults. |
| `instance_rules` / `bot_policy` | Some instances restrict automated/bot accounts or require a bot flag. |
| `content_warning_norms` | Community expectations on CW usage. |
| `rules_checked_at` | Staleness tracking. |

**Questions (Phase 1; answered in the verified table below where marked VERIFIED, the rest stay open):**
- To verify: app registration and OAuth flow per instance, scopes, scheduled-status support, delete/edit, media upload flow.
- To verify: whether the instance exposes per-instance limits via an API endpoint (so the fields above can be refreshed automatically rather than hand-entered).
- To verify: which counts are available (favourites, boosts, replies); impressions/reach are expected unavailable in a federated system — to verify.

**Metrics we want:** `likes` (favourites), `shares` (boosts), `comments` (replies), `followers_gained`, `clicks` (own attribution). `impressions`/`reach`/`views` likely `NULL`.

**Verified 2026-09-27 (Phase 13; all from docs.joinmastodon.org, checked 2026-09-27):**

| Capability | Status | Official source | Paraphrase |
|---|---|---|---|
| Publish | VERIFIED | https://docs.joinmastodon.org/methods/statuses/ | `POST /api/v1/statuses`, scope `write:statuses`; `status`, `visibility` (public / unlisted / private / direct), `language`, `in_reply_to_id` (threads are reply chains), `media_ids[]`; 200 returns the Status (`id`, `url`, `uri`, `created_at`); 401 bad token, 422 validation. `Idempotency-Key` header prevents duplicate submission; keys are kept for up to one hour. |
| Schedule | VERIFIED (not used) | same page | `scheduled_at` at least 5 minutes ahead returns a ScheduledStatus. OmniGrowth keeps publishing from its own outbox at due time (A3), so this is not used. |
| Delete | VERIFIED (not implemented) | same page | `DELETE /api/v1/statuses/:id`, scope `write:statuses`; 404 when not owned. No delete action exists in OmniGrowth yet. |
| Reconciliation | VERIFIED | https://docs.joinmastodon.org/methods/accounts/ and statuses page | `GET /api/v1/accounts/verify_credentials` (`read:accounts` or `profile`); `GET /api/v1/accounts/:id/statuses` (max 40 per page, `max_id` paging, `exclude_reblogs`); `GET /api/v1/statuses/:id/source` (`read:statuses`, since 3.5.0) returns the plain text, so a post can be found by its exact text without re-posting. |
| Metrics | VERIFIED (partial) | https://docs.joinmastodon.org/entities/Status/ | Status has `favourites_count`, `reblogs_count`, `replies_count` (and `quotes_count` since 4.5.0). There is no impressions / reach / views count: those stay NULL. |
| Instance limits | VERIFIED (fields) | https://docs.joinmastodon.org/methods/instance/ | `GET /api/v2/instance` (public) `configuration.statuses.max_characters` (example 500), `max_media_attachments`, `characters_reserved_per_url` (example 23); media and poll limits. Values are per instance; OmniGrowth stores them in the account settings (it does not fetch them). |
| Auth | VERIFIED | https://docs.joinmastodon.org/client/authorized/, https://docs.joinmastodon.org/methods/oauth/ | App registration, `GET /oauth/authorize` + `POST /oauth/token` (`authorization_code`), PKCE (S256) since 4.3.0, granular scopes recommended; token sent as `Authorization: Bearer`. The docs state no token lifetime and no refresh grant: tokens are treated as valid until revoked (`POST /oauth/revoke`). |
| Rate limits | VERIFIED | https://docs.joinmastodon.org/api/rate-limits/ | Default 300 requests / 5 min per account and per IP; media uploads 30 / 30 min; deletes 30 / 30 min; headers `X-RateLimit-Limit`, `-Remaining`, `-Reset` (timestamp); administrators may configure other values. The HTTP status for an exceeded limit is **not stated** on that page: the connector treats 429 as rate limited (retry after `X-RateLimit-Reset`) and any other unexpected answer after sending as uncertain. |
| Media upload | UNVERIFIED | — | Not researched in this session and not implemented: the connector is text-only; media requirements are listed in the preview as "not attached". |
| Cost | VERIFIED (by nature) | https://docs.joinmastodon.org/ | Open API of free server software; no paid tier. Instance rules (automation, bot flag) vary per server and must be checked by the human for the chosen account. |
| Web UI token creation | UNVERIFIED | — | Many servers offer "Preferences > Development > New application"; not found in the official docs, so the setup guide documents the verified OAuth flow and mentions the UI path as unverified. |

Adapter: `omnigrowth/publishing/mastodon.py` (`TEXT_POST`, `THREAD`), behind the A6 live guard, per-account encrypted credential, `docs/connectors/mastodon.md` for setup. Required scopes: `write:statuses read:statuses read:accounts`.

**Fallback:** `ManualExportPublisher` per instance (e.g., if an instance bans bots) + manual metrics entry.

---

## 5. Fallback plan (all platforms)

1. **`MockPublisher`** (`PublishMode.MOCK`, default) — validates, simulates publication, creates synthetic external IDs, stores `publications`, allows fixture analytics (spec §34). Used for all platforms through MVP and until Phase 13 verification.
2. **`ManualExportPublisher`** (`PublishMode.MANUAL`, A6) — renders copy + media checklist; `publication_status = MANUAL_PENDING` until a human marks it published and pastes `canonical_url` (and `external_post_id` if visible).
3. **Manual metrics entry** — human-entered `analytics_snapshots` rows with `MetricSource.MANUAL` (mock and fixture rows use `MOCK` / `FIXTURE`), with unknown fields left `NULL`. Never auto-fabricated.
4. **`<Platform>LivePublisher`** (`PublishMode.LIVE`) — only after that platform's capabilities are `VERIFIED`, A6 conditions all hold, and the human has typed `ENABLE LIVE PUBLISHING FOR <platform>` (AGENTS.md §3).

---

## 6. Verification procedure (Phase 13)

One platform per session. The agent never calls platform endpoints on its own initiative; network access to official *documentation* sites for that session must be explicitly granted by the human.

1. **Scope:** Human names the single platform for the session. Agent records the session start in `ENGINEERING_LOG.md`.
2. **Read official docs only:** For every question in §3 and the platform's section in §4, find the answer in official developer documentation, developer terms/policy, or official help center. Record for each answer: official URL, date checked (`YYYY-MM-DD`), and a one-line paraphrase (no long quotations).
3. **Update this file:** Change each cell to `VERIFIED` or `UNAVAILABLE` with URL + date; leave anything not confirmed as `UNVERIFIED`. Remove or resolve every **[hearsay — unverified]** tag for that platform. Update the summary table and the platform's overall status (a platform's row status becomes `VERIFIED` only when publish, auth, and rate limits are all `VERIFIED`).
4. **Stop and report** (AGENTS.md §7: needs credentials, may cost money): list verified capabilities, costs/tiers, app-review requirements, and a recommendation: live adapter, `ManualExportPublisher`, or both. Wait for human decision.
5. **Implement the `<Platform>LivePublisher`** only for `VERIFIED` capabilities, behind A6 gates, with tests using recorded/mock HTTP fixtures (no live calls in tests). Include failure tests: auth failure (`PLATFORM_AUTH_FAILURE`), rate limit (`PLATFORM_RATE_LIMIT`), timeout (`PLATFORM_TIMEOUT`), unknown outcome (`PUBLICATION_UNCERTAIN`), and the A6 test proving live publishing is impossible when `ENV=test`.
6. **Credentials:** Human creates the developer app / tokens and enters them into `platform_credentials` via the supported path. The agent never enters, handles in plain text, or commits credentials.
7. **First live post:** Human sets `PUBLISH_MODE=live` and `platform_accounts.live_enabled = true` only after typing `ENABLE LIVE PUBLISHING FOR <platform>`; the **human** triggers the first live publish of an `APPROVED` content version. Agent records the result (external ID, URL, any reconciliation) in `ENGINEERING_LOG.md`.
8. **Metrics check:** Run `fetch_metrics` once on the live post; record which `AnalyticsSnapshot` fields came back non-null vs. what this file predicted; correct this file.
9. **Re-verification:** Each `VERIFIED` claim should be re-checked when an adapter error suggests drift, and at least before each major release (interval — see Open questions).

---

## 7. Open questions

1. **RESOLVED (GLOSSARY §4a):** publisher names are `PlatformPublisher` (interface), `MockPublisher`, `ManualExportPublisher`, `<Platform>LivePublisher`.
2. **RESOLVED (for now):** per-subreddit config (Reddit, §4.6) and per-instance config (Mastodon, §4.9) are stored as validated JSON in `platform_accounts.settings`. Revisit dedicated tables only if cross-account querying of these configs becomes necessary.
3. **RESOLVED (GLOSSARY §4a):** `QUOTE_POST` added to `ContentFormat`.
4. **RESOLVED (GLOSSARY §4a):** metrics without a dedicated field (LinkedIn dwell proxies, Substack email open rate, Reddit upvote ratio) go in `analytics_snapshots.platform_metrics` (jsonb).
5. **RESOLVED (GLOSSARY §4a):** snapshot provenance is `MetricSource` (`MOCK, FIXTURE, API, MANUAL`).
6. **Facebook, Rumble, Mastodon formats** are not defined in spec §8; the proposed lists above need human confirmation. Phase 10 writers support these subsets (`config/qa.yaml`): FACEBOOK `TEXT_POST`, `IMAGE_POST`; RUMBLE `LONG_VIDEO_OUTLINE`, `LONG_VIDEO_SCRIPT`, `SHORT`; MASTODON `TEXT_POST`, `THREAD`. `COMMENT_DRAFT`, Facebook `REEL_SCRIPT`/`STORY`/`LONG_VIDEO_OUTLINE`, Mastodon `IMAGE_POST`/`POLL` and Reddit `COMMENT_DRAFT` are not written yet.
7. **RESOLVED (2026-09-27):** Mastodon is in Phase 13 scope; it was chosen as the first connector (free, self-serve, idempotent posting, verifiable reconciliation; see §4.9).
8. **Video/image platforms** (`INSTAGRAM`, `YOUTUBE`, `RUMBLE`, `FACEBOOK` video): we generate scripts, not media. Is a live adapter ever in scope, or is the flow always "human attaches finished media, then publish"? Affects the `ContentAsset.media_requirements` contract.
9. **Paid API tiers** (possibly X, Reddit — unverified): the human must approve any spend (AGENTS.md §7). Budget owner and ceiling?
10. **RESOLVED (GLOSSARY §4a):** `platform_accounts.account_type` (`PlatformAccountType`: `PERSONAL, ORGANIZATION, PAGE, CHANNEL, SUBREDDIT_POSTER, INSTANCE_USER`). Remaining sub-question: Instagram professional (business/creator) accounts have no exact value — map to `ORGANIZATION`, or add one?
11. **Re-verification interval** for `VERIFIED` claims — suggest a `verified_at` staleness threshold; human to set.
12. **RESOLVED (2026-09-27):** the human authorized reading current official API documentation in Phase 13. Read: docs.joinmastodon.org, docs.x.com, learn.microsoft.com (LinkedIn). No platform API endpoint was called.
