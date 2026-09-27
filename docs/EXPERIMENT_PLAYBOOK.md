# Controlled ChatGPT Ads comparison playbook

Eve calls paid tests **controlled comparisons**. The current public OpenAI Ads documentation describes campaign controls, reporting, conversion reporting, and bulk operations, but does not document a native randomized experiment/split-test endpoint. Do not sell, label, or report these as platform-randomized A/B tests.

## Preconditions

Do not register a spend-facing comparison until all are true:

- Client scope, landing page, media ceiling, and written approval are recorded.
- Account review/capability status is checked.
- Relevant tracking has an approved measurement contract.
- One primary KPI, attribution setup, timezone, and decision date are chosen before launch.
- Each arm has a separate, versioned blueprint/hint set and no overlapping change is being made.

## Eve experiment record

`controlled_experiment_register` requires:

| Field | Rule |
| --- | --- |
| Hypothesis | Falsifiable and specific. |
| Arms | Exactly two, with blueprint IDs/version details. |
| Changed variable | One independent variable only. |
| Primary metric | One metric, selected before data collection. |
| Guardrails | Spend, lead-quality, policy, and serving stop conditions. |
| Attribution | Window, source, and known caveats. |
| Timeframe | Start, fixed decision date, and account IANA timezone. |
| Decision rule | Scale, iterate, pause, or inconclusive rule. |

Example:

```json
{
  "name": "Switching-trigger hint comparison",
  "hypothesis": "A switching-specific hint set will improve qualified-demo efficiency versus a general workflow hint set.",
  "changed_variable": "context-hint framing",
  "primary_metric": "qualified_demo_cpa",
  "guardrails": {"maximum_spend": 500, "minimum_lead_quality_rate": 0.5},
  "decision_rule": "Declare only scale, iterate, pause, or inconclusive after the pre-set date and comparable full days.",
  "arms": [
    {"name": "A", "blueprint_id": "...", "hint_set_version": 1, "aggregation_level": "ad_group", "entity_external_id": "adgrp_..."},
    {"name": "B", "blueprint_id": "...", "hint_set_version": 1, "aggregation_level": "ad_group", "entity_external_id": "adgrp_..."}
  ],
  "attribution": {"event": "lead_created", "click_window_days": 30, "view_window_days": 1},
  "timeframe": {"start": "2026-10-01", "decision_date": "2026-10-15", "timezone": "America/New_York"}
}
```

## What to compare first

1. Distinct need/use-case context-hint framing in separate ad groups.
2. One creative proposition once the hint framing is stable.
3. One landing-page message-match change after tracking is stable.
4. Bid strategy only with sufficient credible conversion data.

Do not change creative, budget, context hints, targeting, and landing page in the same comparison. OpenAI also advises avoiding several setting changes when diagnosing delivery; otherwise a later result has no interpretable cause. [Troubleshooting](https://developers.openai.com/ads/troubleshooting)

## Reporting discipline

- Store raw provider response and requested fields with each snapshot.
- Use completed, comparable full days in the account timezone.
- Mark current/recent delivery as unsettled. Impressions/clicks can appear quickly; spend metrics finalize later; conversion data processes daily and can change. [Reporting freshness](https://developers.openai.com/ads/reporting)
- Keep click-through and view-through conversion measures distinct. The dedicated conversion endpoint can report both; selected click/view windows and attribution time basis matter. [Conversion reporting](https://developers.openai.com/ads/reporting)
- A `null` metric is unavailable, not zero.

## Allowed decisions

| Decision | When it is justified |
| --- | --- |
| Scale cautiously | Pre-registered KPI rule and guardrails pass using credible settled data. |
| Iterate | Delivery exists but the hypothesis is weak or a correctable guardrail fails. |
| Pause | Spend/policy/serving/quality guardrail fails. |
| Inconclusive | Insufficient comparable data, partial processing, overlap, or multiple changed variables. |

Never name a “winner” from mock data, partial-day data, an unapproved change, or two arms with different attribution/targeting conditions.

## Recording an outcome in Eve

After the fixed decision date and processing buffer, collect a total-period `insights_sync` snapshot for each registered ad group using the exact pre-registered inclusive date range and account timezone. `controlled_experiment_evaluate` takes a mapping from the registered arm names to those persisted snapshot IDs and checks tenant/entity/period/timezone/provider/aggregation parity. It stores the primary-metric observations and a data-quality assessment only; MCP cannot make or record a final decision.

An admin may record a human `scale`, `iterate`, `pause`, or `inconclusive` decision with `agency experiment-decide` after obtaining a human approval reference and confirming interactively. Mock, unverified, unsettled, null, or otherwise ineligible data can only result in an inconclusive record. The saved decision is a directional operating judgment, never a causal or platform-randomized claim.

Eve conservatively labels live data `unsettled` until 72 hours have elapsed after the inclusive reporting period ends in the account timezone, then `provisionally_settled`. This internal buffer does not promise that attribution will never backfill; preserve snapshots and refresh recent dates before final client reporting. Mock snapshots can only be recorded as `inconclusive`.
