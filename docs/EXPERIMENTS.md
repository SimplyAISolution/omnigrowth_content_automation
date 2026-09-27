# Experiments: design, statistics and interpretation (Phase 9)

Status: implemented 2026-09-26. The binding rules are in `SPEC_AMENDMENTS.md` A14; `STATE_MACHINES.md` §4 describes the lifecycle.

**Code:**
- `omnigrowth/analytics/randomization.py`: the decision test (design-based).
- `omnigrowth/analytics/experiment_stats.py`: descriptive Welch statistics and the sample plan.
- `omnigrowth/services/experiments.py`: lifecycle and analysis.
- `omnigrowth/api/routes/experiments.py`: API.

**Tests:**
- `tests/unit/test_experiment_stats.py`: hand-computed values, published t tables, and an independent numerical integration.
- `tests/unit/test_randomization_inference.py`: exact enumeration, decision/interval agreement, and the power/error study under dependence.
- `tests/integration/test_phase9_experiments.py`: the gate.

## 1. What is measured

| Item | Definition |
|---|---|
| **Unit** | One published post: a content asset on one of the design's platform accounts. |
| **Value per unit** | The primary metric from **one** snapshot, the one captured closest to `observation_hours` after the post's first publication, within `± age_tolerance_hours` (defaults 72 h ± 12 h). The tolerance may be at most observation/4. |
| **Not samples** | Impressions (they are the rate's denominator, inside one unit's value) and repeated analytics pulls. Pulls at other ages are ignored, and one post contributes exactly one value. |
| **Comparable ages** | Every unit is read at the same age, so a post measured after 3 days is never compared with one measured after 3 hours. |
| **Platform and account control** | All units are on the design's one platform and listed accounts. Units are blocked by account, so each treatment is compared with the control within an account and the per-account differences are then combined. Only accounts where every variant has ≥ 2 measured posts are analysed; posts in other accounts are counted as `BLOCK_INCOMPLETE`. The n checked against the requirement, the n tested, and the evidence ids are therefore always the same posts. |
| **Real evidence** | A snapshot with source API or MANUAL on a post that actually went out (publish mode LIVE or MANUAL). A MOCK-published post never reached an audience, so its numbers are simulated whatever their label (`MOCK_PUBLICATION` in the real analysis). The ingest API refuses MANUAL metrics on MOCK publications. |
| **Metric** | A derived-metric key (the brand's active definition, cited with its revision) or a raw snapshot count. Cost and time inputs are not allowed, because they are not audience outcomes measured at an age. |

Units whose value is missing are counted per variant in `excluded` and never imputed:

| Reason | Meaning |
|---|---|
| `NOT_PUBLISHED` | The post was never published. |
| `NOT_YET_AT_AGE` | At evaluation, the post had not yet passed its observation age plus tolerance. With the frozen timeline (§6) this cannot happen for posts published inside the allocation period. |
| `PUBLISHED_AFTER_ALLOCATION_END` | The post went out after `allocation_end_at`. |
| `PUBLISHED_BEFORE_START` | Randomized designs only: the post was exposed before the experiment started, so its exposure could not follow the allocation. |
| `ACCOUNT_MISMATCH` | The post was published on a different account from the one registered for the unit. |
| `NO_SNAPSHOT_AT_AGE` | No snapshot falls inside the age window. |
| `METRIC_NULL` | A snapshot exists, but the metric is NULL (for example, impressions not captured). |

## 2. Assignment

**RANDOMIZED** (the default):
- The server allocates each registered post: blocked equal allocation within each account.
- Within each account, consecutive blocks of V or 2V units (the size drawn per block) contain every variant equally often, in an order drawn from a PRNG seeded by `(design.randomization_seed, account, block)`. The random block size stops the last assignment of a block being predictable.
- A post can be a unit of only one active experiment.
- Allocation is reproducible and auditable: `experiments.allocate(...)` re-derives every assignment, which a test checks.
- A post must be registered **before** it is published.
- The variant's treatment (for example, the question hook) is then applied to that post. A human attests at start that variants differ only in the declared variable.

**OBSERVATIONAL:**
- A human labels existing posts.
- The analysis is the same, but the verdict is always `OBSERVATIONAL_ASSOCIATION`: an association, not an effect.
- The database forbids a winner on such rows (`ck_experiments_observational_has_no_winner`).

## 3. Sample size (fixed before start)

- **Floor:** 30 units per variant. It cannot be set lower; the API returns 422.
- **Power:** 80% (configurable 0.5–0.99) against the pre-registered **minimum meaningful effect** δ (absolute, or relative to the baseline mean).
  - σ is the SD of the metric per post at the design age, taken from the brand's **real** posts on the same accounts, published in the 180 days **before the experiment was created**. The experiment's own posts are excluded. At least 10 posts are needed.
  - n per variant = ⌈2 (z₁₋α/(2m) + z₀.₈)² σ² / δ²⌉, where m = number of treatments. α is split across comparisons (Bonferroni), matching the analysis.
  - The requirement is max(n, 30).
- **No baseline:** without 10 real baseline posts, only the floor applies. The plan says `FLOOR_ONLY_NO_BASELINE`; it is never labelled "powered".
- The plan (σ, δ, α, power, n, basis) is stored in `design.sample_plan` and **frozen** at E2 together with `min_sample_size` and the confidence level.

## 4. Analysis (once, at the fixed endpoint)

- **Stopping rule:** `FIXED_ENDPOINT`.
  - Before `end_at`, evaluation makes no transition (`WINDOW_OPEN`).
  - After `end_at`, the scheduler (or a human request) evaluates **once**, and the result is terminal.
  - There is no sequential testing, so there is no "stop at the first significant look".
- **Decision test: randomization inference (design-based).**
  - Posts from the same account are **not** independent: they share an audience, follower growth and news cycles, and they are serially correlated. Comparing posts within an account removes account-level differences, but it does **not** make the posts independent, so a Welch t-test's independence assumption does not hold.
  - OmniGrowth performed the random allocation itself. Under the sharp null of no effect for any post, every allocation the design could have produced is equally likely, **whatever the correlation between posts**. The test therefore re-draws the allocation exactly as it was made: labels are shuffled within each randomization run (V or 2V consecutive posts in one account). It compares the observed statistic with 9,999 such re-draws: p = (1 + #{|D*| ≥ |D|}) / (N + 1).
  - **Statistic, and how accounts combine:** per account, the treatment mean minus the control mean. Accounts are combined as Σ wₐ dₐ / Σ wₐ with wₐ = n_c·n_t/(n_c+n_t), the harmonic sample size of the account, so an account contributes in proportion to how much information it has about the difference. Only accounts where every variant has ≥ 2 measured posts are included.
  - **Interval:** inversion of the same test under a constant-shift model: all effects d for which "effect = d" is not rejected. It is found by bisection on the same re-draws, so a result is significant exactly when its interval excludes 0.
  - Welch figures (standard error, df, p, interval) are still reported, under `welch_descriptive`, labelled as assuming independence and **not** used for the decision. Relative lift and Hedges' g come from the same stratified estimator.
- **Multiplicity:** Bonferroni for both the decisions and the intervals: each comparison is tested at α/m, and its interval is at level 1 − α/m.
- **Several significant treatments:** the best one wins only if it also beats every other significant treatment in pairwise randomization tests (Bonferroni over those pairs). Otherwise the verdict is `TREATMENTS_BEAT_CONTROL_UNRANKED`, and the pairwise results are reported in `real.pairwise_between_winners`.

### Does the power calculation match the analysis?

- The plan uses the independence-based normal-approximation formula, **multiplied by a pre-registered inflation factor** (`design.power_inflation`, default **1.25**) for dependence the formula ignores. The requirement is max(inflated n, 30).
- This was checked by simulation with `uv run python scripts/simulate_experiment_power.py --reps 400`, record `docs/audit/experiment_power_simulation_20260927T030535Z.json`.
- **Setup:** two accounts, each with its own level (SD 3) and a follower-growth trend. Units were allocated by the production routine and tested with the production randomization test (499 re-draws in the simulation). The plan used σ = 1 and δ = 0.6, giving 44 per variant from the formula and 55 after inflation. Each rate is from 400 simulated experiments (Monte Carlo SE ±0.02 for power, ±0.011 for type I):

  | Scenario | Power at 44 (no inflation) | Power at 55 (×1.25) | Type I | Welch (old): power / type I |
  |---|---|---|---|---|
  | Independent posts | 0.77 | **0.87** | 0.055 | 0.85 / 0.045 |
  | AR(1) ρ = +0.5 | 0.95 | **0.97** | 0.045 | 0.89 / 0.005 |
  | AR(1) ρ = +0.8 | 1.00 | **1.00** | 0.033 | 0.98 / 0.000 |
  | AR(1) ρ = −0.3 | 0.69 | **0.83** | 0.040 | 0.85 / 0.053 |
  | Level shift mid-way | 0.74 | **0.84** | 0.055 | 0.46 / 0.000 |
  | Effect differs by account (0.3 vs 0.9) | 0.85 | **0.91** | 0.048 | 0.87 / 0.025 |

- A separate 2,000-replication check of the type I error gave 0.046 (independent) and 0.051 (ρ = −0.3), both nominal.
- **Reading:**
  - Without inflation, the plan **fell short** of 80% under negative serial correlation (0.69) and a level shift (0.74).
  - With the default 1.25 it met the target in **every simulated scenario**.
  - The randomization test's error rate stays at the nominal 5%. Welch's does not: it becomes badly miscalibrated under positive correlation and level shifts.
- **Limits of this check:** these six structures are the ones tested. Stronger negative correlation, larger shifts, or very unequal accounts can still reduce power below plan; raise `power_inflation` if the brand's history suggests them. σ from pooled per-post history includes between-account variation, which errs toward larger samples.

- **Reported for each comparison:**
  - effect sizes: the difference, the relative lift over the control mean, and Hedges' g (pooled within-block SD, small-sample corrected);
  - uncertainty: the randomization interval with its level (decision), plus the descriptive Welch standard error, df and interval;
  - p-values: raw and adjusted;
  - flags: `significant` and `favourable`, plus `method` (re-draws, blocks, accounts).
- **Reported for each variant:** n, mean, SD, exclusions, whether the requirement is met, and the snapshot ids used.

### Verdicts

| Verdict | State | When |
|---|---|---|
| `WINNER` | CONCLUDED | Randomized, real data, every variant n ≥ requirement. Either exactly one treatment has randomization p ≤ α/m in the favourable direction, or the best of several such treatments also beats each of them pairwise. If instead every treatment is significantly *worse*, the control wins. |
| `TREATMENTS_BEAT_CONTROL_UNRANKED` | INCONCLUSIVE | Several treatments beat the control, but none is distinguishable from the others. |
| `NO_DETECTABLE_DIFFERENCE` | INCONCLUSIVE | Sufficient randomized real data, but nothing is significant. This is not proof of "no difference": see the interval. |
| `INSUFFICIENT_EVIDENCE` | INCONCLUSIVE | Some variant is below the frozen requirement, or no comparison is estimable. |
| `NO_REAL_DATA` | INCONCLUSIVE | No real (API/MANUAL) snapshot at the design age. |
| `OBSERVATIONAL_ASSOCIATION` | INCONCLUSIVE | A nonrandomized design, however strong the association. |

**How to read the numbers.** A 95% interval is a statement about the procedure: across repeated experiments, intervals built this way cover the true difference 95% of the time. It is **not** a 95% probability that a variant is better, and the API response says so (`interpretation`).

## 5. Simulated evidence

- MOCK and FIXTURE snapshots are analysed **separately** into `result.simulated`, with a note. Each unit's value comes from its closest simulated snapshot.
- Simulated evidence never enters the verdict, the winner, the stored `sample_size`/`observed_value`, or `evidence_snapshot_ids`.
- Until Phase 13 adapters exist, every mock-published post therefore yields `NO_REAL_DATA`. That is intended.
- Nothing changes production strategy automatically:
  - no experiment result or recommendation edits campaigns, prompts or schedules;
  - acting on a result is a human decision;
  - recommendations record `evidence_query.simulated_evidence`.

## 6. Lifecycle and API

| Step | Endpoint | Notes |
|---|---|---|
| E1 | `POST /experiments` | Design and variants (the first is the control). The campaign must be ACTIVE or PAUSED. The accounts must belong to the brand and the platform. |
| Units | `POST /experiments/{id}/units` | Allowed in DRAFT or RUNNING. Randomized units are allocated by the server and must not be published yet. Observational units carry `variant_label`. |
| E2 | `POST /experiments/{id}/start` | Guards: attestation; `allocation_end_at` still in the future; each variant has a unit; scheduled analytics pulls fall inside every measurement window (2 × tolerance ≥ pull interval); and the analytics lag covers the pull interval. Then the sample plan is frozen. The confidence level is fixed at 0.95. |
| E3 / E6 | scheduler tick | E3 → COLLECTING when every variant has a published unit. E6 → INCONCLUSIVE if `allocation_end_at` passes first. |
| E4 / E5 | `POST /experiments/{id}/evaluate` [IK] or the scheduler | An async `EXPERIMENT_EVALUATION` job on the `analytics` queue. |
| Results | `GET /experiments/{id}/results` | Verdict, method, real and simulated analyses, evidence ids, interpretation. |
| E7 | `POST /experiments/{id}/cancel` | |

### Timeline (frozen at creation, checked at start)

| Point | Meaning |
|---|---|
| `allocation_end_at` | Set by the user. The last moment a unit may be registered or published. Registration after it returns 409. Posts published later are excluded as `PUBLISHED_AFTER_ALLOCATION_END`. |
| `last_measurement_window_closes_at` | `allocation_end_at` + observation + tolerance: the last post's window closes. |
| `collection_cutoff_at` | That, plus `analytics_lag_hours` (default 24 h; must be at least the analytics pull interval). This is the time for the last snapshot to be pulled or ingested. |
| `evaluation_at` = `end_at` | = `collection_cutoff_at`. The scheduler evaluates once at or after it; any earlier request gets `WINDOW_OPEN`. |

A post published at the very last moment of the allocation period is therefore fully measured; a test checks exactly that case.

**Deviations from API_PLAN §2.11:**
- Variants are created inline with the experiment.
- `PATCH /experiments/{id}`, the variant PATCH, and `POST /variants` are **not implemented**, because the design is fixed at creation. Changing it means creating a new experiment (STATE_MACHINES Q7).
- `ExperimentResultRead` carries `real`/`simulated` blocks instead of flat per-variant rows.
- Units are a new resource, added by A14.
- `ExperimentCreate` takes `allocation_end_at` instead of `end_at`; `end_at` is derived.

## 7. Limitations

1. **The randomization test covers the sharp null only.** It is valid for "no effect on any post" under any dependence. The interval assumes a constant (shift) effect; if the effect differs strongly between posts or accounts, the interval is approximate.
2. **Missing values are dropped** (no snapshot at the post's age, NULL metric). The test stays valid only if missingness is unrelated to the variant. Exclusions are reported per variant for inspection.
3. **Cross-posting the same content to several accounts is not modelled.** The same text posted on two accounts gives two units whose outcomes may be linked.
4. **Heavy-tailed metrics** (a rare viral post) keep the test valid but reduce power. Look at the per-unit values (evidence ids).
5. **Bonferroni is conservative when m > 1.** Planning and analysis both use it, so power is as planned but the tests are slightly conservative.
6. **Real data is still missing.** The baseline needs 10 real posts. Before Phase 13 there is no API source, only MANUAL entry, so plans are usually floor-only.
7. **`POSTING_TIME` experiments are refused (422).** With a fixed allocation end, which posts miss it would depend on each variant's time slot, so exclusions would depend on the label and bias the comparison. They need a design where every variant's slots fall inside the allocation period; not built yet.
8. **Treatment compliance is not verified.** Whether a post really used its assigned variant (for example, the question hook) rests on the human attestation.
