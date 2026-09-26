# Metrics, derived metrics and attribution (Phase 8)

Status: implemented 2026-09-26 (spec §16, §17; API_PLAN §2.10, §2.7). Code: `omnigrowth/analytics/formula.py`, `analytics/metrics.py`, `services/metric_definitions.py`, `services/analytics.py`, `services/calendar.py`.

## 1. Raw metrics (analytics snapshots)

One immutable row per `(publication_id, captured_at)`.

| Metric | Meaning | Rules |
|---|---|---|
| `impressions`, `views`, `reach`, `likes`, `comments`, `shares`, `saves`, `clicks`, `profile_visits`, `conversion_count` | Platform counts as captured at `captured_at` | ≥ 0 or NULL |
| `followers_gained` | Net followers gained | May be negative |

**NULL means "not captured", never 0.** A platform that does not report saves stores NULL, and NULL is never coerced to 0 anywhere: not on ingest, not in totals, and not in derived metrics.

Every snapshot carries a **`source`**:

| Source | Meaning | Real? |
|---|---|---|
| `MOCK` | Deterministic numbers from `MockPublisher.fetch_metrics` (seeded by content hash and platform) | **Simulated** |
| `FIXTURE` | Uploaded test/fixture data (`POST /analytics/snapshots`) | **Simulated** |
| `MANUAL` | A human typed in numbers read from the platform | Real (human-reported) |
| `API` | Fetched from a platform API (Phase 13 adapters; none exist yet) | Real |

`MOCK` and `API` can only come from `/analytics/pull`. Uploads cannot claim them.

## 2. Keeping simulated data apart from real data

- Every analytics response carries a **`provenance`** block: `sources` (counts per source), `simulated`, `real`, and a `note`. The note reads "Simulated data (MOCK/FIXTURE): not real platform performance." When simulated and real data are mixed, it warns and suggests filtering.
- `GET /analytics/campaigns/{id}/report` and `GET /analytics/attribute-comparisons` accept a `sources=` filter, for example `sources=API&sources=MANUAL` for real data only.
- Each per-publication report row and each comparison group lists the source(s) of its snapshots.
- Recommendations record `evidence_query.data_sources` and `evidence_query.simulated_evidence`. In the MVP, every recommendation rests on MOCK data and says so.
- Until Phase 13, **all** stored metrics are simulated or human-entered. No number in this system is real platform performance unless its source is `API` or `MANUAL`.

## 3. Derived metrics (spec §17)

Definitions are **data**: rows in `derived_metric_definitions`. The defaults are seeded as global rows (`brand_id NULL`, revision 1). An OWNER can:

- add a brand definition (`POST /analytics/derived-metric-definitions`). A brand row with the same `key` overrides the default for that brand;
- revise any active definition (`PATCH …/{id}`). This creates revision `n+1` and deactivates revision `n`. Rows are never edited in place. Every report lists the `definition_id`, `key` and `revision` it used (`definitions[]`, and `definition` on comparisons).

### Defaults

| Key | Formula | Notes |
|---|---|---|
| `engagement_rate` | `captured(likes, comments, shares, saves) / impressions` | Uses the interactions that were captured and lists the missing ones |
| `share_rate` | `shares / impressions` | |
| `save_rate` | `saves / impressions` | |
| `comment_rate` | `comments / impressions` | |
| `click_rate` | `clicks / impressions` | |
| `follow_conversion` | `followers_gained / profile_visits` | Net; can be negative |
| `content_efficiency` | `(w.reach·reach + w.engagement·captured(comments, shares, saves) + w.conversion·conversion_count) / (1 + w.cost·creation_cost_usd + w.time·production_minutes)` | Weights: reach 0.001, engagement 1, conversion 5, cost 10 per USD, time 0.1 per minute |

**`content_efficiency` is not an objective score** (spec §17: "do not pretend a single universal performance score is objectively correct"). Its weights are a starting point. Change them per brand with PATCH. "Meaningful engagement" deliberately leaves out likes. The `1 +` in the denominator keeps a free, instant local generation from dividing by zero. It also means that at local-model cost (USD 0) the score is driven by outcomes and time.

### Formula grammar (never `eval`)

`ast.parse(mode="eval")` is only used to *read* the text. A whitelist interpreter then evaluates it.

**Allowed:**
- numbers
- the metric names in §1, plus `creation_cost_usd` and `production_minutes`
- weights `w.<name>`, which must be declared in `weights`, and every declared weight must be used
- `+ - * /`, unary minus and parentheses
- `captured(m1, m2, …)`

**Refused:** everything else (calls, attributes, `**`, comparisons, strings, unknown names, more than 64 nodes, nesting deeper than 10, more than 1,000 characters). An invalid formula returns 422 `INVALID_FORMULA`.

### NULL and zero rules (Phase 8 gate)

| Situation | Result |
|---|---|
| A metric outside `captured()` is NULL | NULL, `null_code=MISSING_INPUT`, `null_reason="<metric> not captured"` |
| `captured(...)` with some inputs present | Sum of the present inputs; the rest are listed in `missing_inputs` |
| `captured(...)` with no input present | NULL, `MISSING_INPUT` |
| Denominator is 0 (at any depth) | NULL, `null_code=ZERO_DENOMINATOR`, never an exception or ∞ |
| Non-finite result | NULL, `ZERO_DENOMINATOR` |

### Aggregates (campaign, platform, format)

1. Rows missing a strictly required metric (any metric outside `captured()`, for example the denominator) are left out. A metric used strictly is therefore present in every remaining row and may also be used inside `captured()`.
2. Inside `captured()`, an input is used only if **every** remaining row captured it. One platform's missing `saves` must not count as 0 against its impressions.
3. The aggregation method, reported as `aggregation` on every aggregate value, depends on the formula:
   - **`RATIO_OF_SUMS`** applies to formulas without numeric constants, which is every rate. Inputs are summed and the formula is applied once: sum(clicks) / sum(impressions), not a mean of rates.
   - **`MEAN_OF_ROWS`** applies to formulas with a constant, such as the `1 +` in `content_efficiency`. Summing would count the constant once for N posts, so the value would grow with group size. The aggregate is instead the mean of the per-publication values that are not NULL.

`totals` are column sums, and a metric no row captured stays NULL.

## 4. Attribute comparisons (spec §16)

`GET /analytics/attribute-comparisons?brand_id=&attribute=&metric=[&platform=&campaign_id=&since=&until=&sources=&min_sample=]` groups PUBLISHED publications by one attribute:

| Attribute | Source |
|---|---|
| `topic`, `pillar`, `audience` | `content_assets` |
| `hook_type`, `cta_type`, `media_type` | Version that was published |
| `platform`, `format` | Asset |
| `word_count_bucket` | `<50`, `50-99`, `100-199`, `200-399`, `400+` |
| `posting_hour` | Hour of `published_at` in the campaign's time zone |
| `campaign` | Campaign id |
| `model` | Model of the published version's generation call |
| `prompt_version` | `domain/name@vN` |

`metric` is a derived key (the brand's active definition) or a raw input. Each group reports:

- `n` (publications with a non-NULL value)
- `excluded_null` (publications with data whose value is NULL: they never enter the statistics)
- mean and median
- `sufficient` (`n ≥ min_sample`, default 5)
- the `snapshot_ids` used as evidence
- `sources`

Publications without a snapshot in the window are left out. The response always carries the note "Correlational, not causal".

## 5. Attribution: what the numbers do and do not mean

1. **Latest snapshot only.** A publication contributes its most recent snapshot inside `[since, until]` (and `sources`). Counts are cumulative platform totals at that moment, not increments. Two publications captured at different ages are compared as-is. There is no normalization for time since posting. Compare within similar windows.
2. **Publication-level, last-touch.** Outcomes are attributed to the single publication whose metrics they are. There is no cross-post, multi-touch, view-through or platform attribution window, and nothing links a conversion on a website to a post. `conversion_count` is whatever the source reported.
3. **Effort attribution is partial.**
   - `creation_cost_usd` and `production_minutes` sum the model calls of the **writer runs** of the asset's versions, including repairs and regenerations.
   - Research, strategy and planning calls are campaign-wide and are **not** split across posts.
   - Human editing time is not measured.
   - Local models cost USD 0 (A1). A remote call without configured prices makes the cost NULL, not 0.
   - A purely human-written asset has no writer run, so its effort is NULL.
4. **Correlational.** Comparisons and reports show association only. Topic, timing, audience and platform algorithm changes all move together. Use Phase 9 experiments (one declared variable, frozen sample and confidence thresholds) to test a hypothesis.
5. **Small samples.** `sufficient` is false below `min_sample`. The campaign report also exposes `sufficiency.satisfied`. Top and bottom lists never overlap: each is at most half the ranked publications.
6. **Simulated data.** MOCK numbers are deterministic functions of content hash and platform. They exercise the pipeline and do **not** describe audience behaviour. Any conclusion drawn from them is about the software, not the market.
7. **Deleted posts.** Only PUBLISHED publications are pulled or reported. A post marked DELETED (P9) stops being polled (X9), and its earlier snapshots are kept.

## 6. Scheduled pulls

On each tick, the scheduler enqueues one `ANALYTICS_PULL` job (actor `SCHEDULER`) for each campaign that meets all of these:

- it has a post published within `analytics.window_days` (default 30);
- no pull for it is queued or running;
- no pull for it was queued within `analytics.interval_minutes` (default 360).

At most `max_campaigns_per_tick` (10) campaigns are handled per tick. Settings live in `config/publishing.yaml` → `analytics`. Before Phase 13, a pull fetches MOCK metrics for MOCK publications only; anything else is reported as `skipped: NOT_IMPLEMENTED`.

## 7. Calendar (§13)

`GET /calendar?view=day|week|month&date=YYYY-MM-DD&tz=<IANA>` returns two kinds of card:

- publication jobs by `due_at` (CANCELLED jobs hidden);
- plan items that have no live publication job.

The range is computed from local midnight in `tz`, and weeks start on Monday. At most 2,000 items are returned; more gives 422. Rescheduling goes through `POST /approvals/{id}/actions` (`SCHEDULE`, CA12).
