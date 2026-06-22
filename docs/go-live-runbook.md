# Go-Live Runbook — Amazon Pricing & Order Sync

Post-incident (2026-06-10: £0 purchase costs pushed to Amazon → ~500 below-cost orders). This runbook is the gate for the next live attempt. Owner signs each section. **Two-person rule: no switch flips without a second person confirming the readiness reports are clean.**

## 0. Code prerequisites (merge before anything)

| PR | What | Why |
|---|---|---|
| #91 | Pricing validators, Stock Only mode, deviation guard | Blocks £0-cost/invalid min-max from push |
| #90 | AFN stock sync → FBA strategy selection | Root cause #1 of incident |
| #89 | Feed status poller + failed-sync bulk retry | FATAL feeds noticed in minutes; recover 305 lost orders |
| #88 | Bundle log consolidation, cost filter | Error-log signal/noise |
| #92 | Go-live monitoring reports | The dashboards below |

Still-open gaps found in audit (fix or accept explicitly before go-live):
1. **`submit_to_amazon` doc button bypasses `enable_pricing_sync`** (amazon_product_pricing.py:75). Gate it.
2. **`manual_price_push.py` bypasses every guard** — the emergency tool must run validators + default to dry_run.
3. **No transport-level freeze switch** — add one check inside `Feeds.create_feed` + repricer POST that stops ALL outbound writes.
4. **Order silently lost when getOrderItems returns empty** (amazon_repository.py:289) — create failed-sync record instead of `return None`.
5. **Cron race**: strategy refresh and pricing submit fire on the same */30 tick — stagger (refresh :00/:30, submit :15/:45); add a disable flag to `bulk_refresh_strategies`.
6. **SP-API tokens leak into Error Log tracebacks** — scrub request headers.
7. **CI installs unpinned erpnext** (ci.yml) while prod runs v16 — pin the branch.

## 1. External safety net (do FIRST — independent of our code)

- **Set Seller Central per-SKU Minimum/Maximum price bounds** (Pricing → Manage Pricing, bulk file). Out-of-bounds price → listing deactivates instead of selling. This alone caps a June-10 repeat. Caveats: bounds exclude shipping (Repricer's include it — keep SC bounds wider); once set they can only be widened, never removed.
- Verify Repricer behaviour when min is missing/0 on a canary BEFORE go-live.
- Feeds: max 5 per 5 min, up to 25k SKUs each — one big feed beats many small. `cancelFeed` only works on still-queued feeds; it is NOT a price revert.

## 2. Pre-go-live data gate (reports must be clean)

Run and clear, in order:
1. **Amazon Go-Live Data Readiness** (#92) — 0 BLOCKED rows among SKUs going live (currently 4,720 blocked on test data).
2. **Amazon Pricing Push Readiness** (#91) — 0 violations (currently: 2,224 zero-cost, 391 no-cost-row, 1,215 min==max).
3. **Supplier Data Readiness** (#92) — no stale supplier (>7 days) among active suppliers.
4. `bulk_fetch_selling_prices` run so every SKU has `current_selling_price` (deviation guard baseline; 4,122 currently missing).
5. AFN stock sync ran (`enable_afn_stock_sync=1`) and FBA strategies actually selected — spot-check 10 known-FBA SKUs.
6. Failed Sync Records drained to 0 via bulk retry (#89).
7. **Strategy Price Comparison** report — variance between our computed prices and live Amazon prices explained for every SKU in the first wave.

## 3. Config checklist (production values at cutover)

| Flag | Value | When |
|---|---|---|
| encryption key decrypt test (SP-API, Repricer, Email creds) | pass | after any restore — known failure mode |
| `amazon_publishing_mode` | **Stock Only** | always (prices go via Repricer only) |
| `enable_sync` (orders) | 1 | at cutover time agreed with ops |
| `enable_afn_stock_sync` | 1 | ≥1h before pricing enabled |
| `enable_pricing_sync` | 1 | only after section 2 green |
| `pricing_publishing_mode` | Publish Selected Prices Only | during canary; All after ramp |
| deviation guard | direction Both, threshold ≤10% | always |
| `repricer_enabled` | 1 | with pricing |

After flipping any flag: `bench clear-cache` and read the value back from console (cached Singles can serve stale values to workers).

## 4. Canary protocol (the test June 10 never had)

~14 real low-velocity SKUs chosen by **cost-source × strategy coverage**, not convenience:
- every cost source WITH and WITHOUT backing data — *PO-Sync with no PO history must be BLOCKED, not £0*;
- every strategy incl. backorder-with-zero-stock-and-no-restock-date; one bundle (one with an out-of-stock component), one pool item, one pack UOM.

Per push: readiness reports clean → dry feed (`test_mode=True`) diffed against expected → push → poller DONE → **parse result document, assert zero per-message errors** (a DONE feed can still reject SKUs) → read back live price/min/max to the penny (include one SKU just above the £10 commission threshold) → Below Min Price Sales report empty.

Ramp: 14 canaries → 24h soak → 100 SKUs sampled across cost sources → full catalog. **Stop conditions:** any £0/near-£0 min in a built feed; deviation-guard block rate above expected; any feed message error; any Below-Min sale.

## 5. During go-live: monitoring rota

Every 30 min, first 48h (assign a named person per shift):
- **Below Min Price Sales** (#92) — must stay empty. Any row = incident, go to section 6.
- **Amazon Pricing Push Monitor** (#92) — no Error rows, nothing pending >60 min.
- **Amazon Order Sync Health** (#92) — orders flowing hourly, open failed syncs not growing, minutes-since-last-order < 120 during business hours.
- Error Log — zero new methods vs baseline.

## 6. Incident playbook (prices wrong on Amazon)

1. **Freeze** (minute 0): `enable_pricing_sync=0` + `repricer_enabled=0` (+ transport freeze switch once built) + clear cache; cancel queued feeds (`cancelFeed` — only pre-processing ones).
2. **Assess** (minutes 0–10): Below Min Price Sales + Push Monitor → affected SKU list and damage rate.
3. **Revert prices** (minutes 10–30): small set → Listings Items PATCH per SKU (near-real-time); large set → emergency feed from the **pre-written known-good CSV** (regenerate this snapshot before every ramp step). Verify SC min/max bounds deactivated the rest.
4. **Orders**: do NOT mass-cancel (cancellation rate >2.5% threatens account health). Triage per order; eat small losses, escalate big ones.
5. **Record**: timeline + affected orders into the incident doc while fresh.

## 7. Rollback matrix

| Subsystem | Revert | Time |
|---|---|---|
| App code | Frappe Cloud: redeploy previous version | ~10–15 min |
| Database | On-demand backup taken AT cutover → restore; **copy encryption key into site_config before use** | 30–60 min |
| Prices on Amazon | known-good CSV via (guarded) manual push / emergency feed | 15–30 min |
| Prices on Repricer | re-sync known-good min/max snapshot | ~15 min |
| Stock on Amazon | re-run stock feed from ERP | ~15 min |
| Orders | `enable_sync=0`; orders queue at Amazon, resume later | instant |
| Strategy switching | no freeze flag yet — build (gap #5) | — |

Patches are one-way: rollback = backup restore + previous deploy, never "migrate down". Take the on-demand backup immediately before flipping any switch.

## 8. Post-go-live (first 2 weeks)

- Daily: Below Min Price Sales (days_back=1), readiness reports re-run, error-log digest.
- Reconciliation: weekly diff of pushed prices vs Amazon merchant listings report (building block: `bulk_fetch_selling_prices`) — alert on drift.
- Circuit breaker (build): auto-flip pricing sync off when a feed run blocks >N% of SKUs on validation/deviation.
- Keep legacy API in read-only parallel-run for 1 week; compare via Strategy Price Comparison.
