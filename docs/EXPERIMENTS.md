# Experiments: design, statistics and interpretation (Phase 9)

Status: implemented 2026-09-26. The binding rules are in `SPEC_AMENDMENTS.md` A14; `STATE_MACHINES.md` §4 describes the lifecycle.

**Code:**
- `omnigrowth/analytics/experiment_stats.py`: pure statistics.
- `omnigrowth/services/experiments.py`: lifecycle and analysis.
- `omnigrowth/api/routes/experiments.py`: API.

**Tests:**
- `tests/unit/test_experiment_stats.py`: hand-computed values, published t tables, and an independent numerical integration.
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
| `NOT_YET_AT_AGE` | At evaluation, the post had not yet passed its observation age plus tolerance. |
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
- **Test:** two-sided Welch t-test of each treatment against the control, on the per-unit values.
  - Blocks (accounts) where both arms have ≥ 2 units are combined with weights w = n_c·n_t/(n_c+n_t).
  - Variance = Σ(w/W)²(s_c²/n_c + s_t²/n_t), with Welch–Satterthwaite degrees of freedom.
  - With one account this is exactly the Welch test.
- **Multiplicity:** Bonferroni for both the decisions and the intervals (level 1 − α/m). A result is significant exactly when its interval excludes 0, so the two can never disagree. Holm would be slightly more powerful, but it has no matching intervals.
- **Several significant treatments:** the best one wins only if it also beats every other significant treatment in pairwise Welch tests (Bonferroni over those pairs). Otherwise the verdict is `TREATMENTS_BEAT_CONTROL_UNRANKED`, and the pairwise results are reported in `real.pairwise_between_winners`.
- **Reported for each comparison:**
  - effect sizes: the difference, the relative lift over the control mean, and Hedges' g (pooled within-block SD, small-sample corrected);
  - uncertainty: the standard error, df, and the interval with its level;
  - p-values: raw and adjusted;
  - flags: `significant`, `favourable`, and `blocks_used`.
- **Reported for each variant:** n, mean, SD, exclusions, whether the requirement is met, and the snapshot ids used.

### Verdicts

| Verdict | State | When |
|---|---|---|
| `WINNER` | CONCLUDED | Randomized, real data, every variant n ≥ requirement. Either exactly one treatment has adjusted p < α in the favourable direction, or the best of several such treatments also beats each of them pairwise. If instead every treatment is significantly *worse*, the control wins. |
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
| E2 | `POST /experiments/{id}/start` | Guards: attestation, `end_at` ≥ now + observation + tolerance, each variant has a unit. Then the sample plan is frozen. The confidence level is fixed at 0.95. |
| E3 / E6 | scheduler tick | E3 → COLLECTING when every variant has a published unit. E6 → INCONCLUSIVE if the window closes first. |
| E4 / E5 | `POST /experiments/{id}/evaluate` [IK] or the scheduler | An async `EXPERIMENT_EVALUATION` job on the `analytics` queue. |
| Results | `GET /experiments/{id}/results` | Verdict, method, real and simulated analyses, evidence ids, interpretation. |
| E7 | `POST /experiments/{id}/cancel` | |

**Deviations from API_PLAN §2.11:**
- Variants are created inline with the experiment.
- `PATCH /experiments/{id}`, the variant PATCH, and `POST /variants` are **not implemented**, because the design is fixed at creation. Changing it means creating a new experiment (STATE_MACHINES Q7).
- `ExperimentResultRead` carries `real`/`simulated` blocks instead of flat per-variant rows.
- Units are a new resource, added by A14.

## 7. Limitations

1. **Within-account correlation over time** (trends, audience fatigue) is handled only by randomization within the account. There is no time-series or mixed model.
2. **Posts on different accounts** are treated as independent given the blocking. Cross-posting the same content to several accounts is not modelled.
3. **Welch is used as a large-sample test.** With the 30-unit floor it is robust to moderate skew in rates. Very heavy-tailed metrics (a rare viral post) can still distort means. Look at the SD and the per-unit values (evidence ids).
4. **Bonferroni is conservative when m > 1.** Planning and analysis both use it, so power is as planned but the tests are slightly conservative.
5. **Real data is still missing.** The baseline needs 10 real posts. Before Phase 13 there is no API source, only MANUAL entry, so plans are usually floor-only.
6. **Treatment compliance is not verified.** Whether a post really used its assigned variant (for example, the question hook) rests on the human attestation.
