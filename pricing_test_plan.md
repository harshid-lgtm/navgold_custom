# Amazon Pricing — Adversarial E2E Test Plan

**Site:** `navgold.localhost:8010` · **Creds:** `Administrator` / `Frappe@123`
**Goal:** break the pricing pipeline end-to-end — from cost rows → strategy selection → push-block guards → outbound Amazon feed payload. Each row below is one complete, self-contained test case: **Setup → Action → Expected (invariant that must hold)**. A test *fails* (bug found) if the observed behaviour differs from **Expected**.

## How to run each case
1. Drive it through the real controllers/whitelisted methods (not by hand-writing to the DB), so `validate` / `populate_costs` / strategy-selection / push-guards actually fire.
2. Prefer `bench --site navgold.localhost console` for setup + assertions; use the Desk UI at `:8010` only where the case is explicitly a UI flow.
3. **Isolate + roll back**: create throwaway records with a `ZZTEST-` name prefix and delete them at the end; never mutate a real production Proxy SKU / Pricing. Snapshot any Navgold Settings flag you flip and restore it.
4. Record: case ID, PASS/FAIL, the observed value vs expected, and the exact repro (doc names, field values).

## Reference — the invariants under test
- **Price formula:** `price = numerator / (denominator − margin)`; Absolute→numerator, Relative(%)→denominator, VAT→`denominator −= vat/(1+vat)`; high-price adjustment when `price > commission_threshold` (default 10).
- **Strategy priority:** FBA (`fba_stock>0`) → SFP (`total_stock>0`) → Backorder (`has_restock_date` or `total_stock<=0`) → Default.
- **Push-block (per active strategy / pricing):** zero % cost (Relative/VAT==0); Auto fed purchase-cost==0 (unsynced); Auto defaulted cost==0; no/disabled/missing active strategy; `min<=0`; `max<=0`; `min>max`; `min==max` without `fixed_price`; **hard floor** `min <= purchase_cost`.
- **Kill switches:** `freeze_amazon_outbound` (AmazonFrozenError), `pricing_sync_mode != "Live"` (PricingNotApprovedError).
- **value_mode:** Auto = feed/default re-derives on every save; Manual = sticky, feeds never overwrite; external edit to an Auto row → pinned Manual; Amazon-Sync/FBA-fee externally-owned rows are never pinned or overwritten.

---

## A. Price-calculation variations (formula stress)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| A1 | Strategy with one Absolute purchase cost = 5, min_margin 10%, max_margin 30% | Save strategy | min/max recomputed with `5/(1−margin)`; min ≤ max; both > 0 |
| A2 | Strategy where sum of Relative costs + margin ≥ 100% (denominator − margin ≤ 0) | Save | Price returns 0 (no negative/inf price); pricing gets a push-block, not a crash |
| A3 | VAT cost = 20% alone with a purchase cost | Save | `denominator −= 0.20/1.20`; price finite and > purchase cost |
| A4 | Purchase cost tuned so computed `initial_price` crosses `commission_threshold` (10) | Save | High-price branch applies `high_price_adjustment` (0.07); price jumps at the threshold boundary; verify just-below vs just-above |
| A5 | min_margin > max_margin | Save | `validate_margin` throws "Min Margin cannot be greater than Max Margin" (blocked at save) |
| A6 | Negative cost value (e.g. −5 Absolute) | Save | Does not silently produce a negative published price; either rejected or floored — assert min>0 guard fires downstream |
| A7 | Extremely large Absolute cost (1e9) and tiny margin | Save | No overflow; `flt(...,2)` rounding stable; min ≤ max |
| A8 | Two Absolute + one Relative + one VAT together | Save | Order-independent: numerator = Σabsolute, denominator = 1 − Σrelative − vat_fraction; matches hand calc to 2dp |
| A9 | margin exactly equal to denominator (adjusted_denominator == 0) | Save | Price = 0, not division-by-zero |
| A10 | Reverse margin: set `target_price` below total cost | Save | `calculated_margin`/`calculated_profit` negative and consistent with forward formula; no crash |

## B. Strategy selection & switching
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| B1 | Pricing with all 4 strategies active, FBA stock = 3 | Recompute active strategy | Selects **FBA** |
| B2 | FBA stock = 0, MFN/total stock = 5, no restock | Recompute | Selects **SFP** |
| B3 | FBA=0, total=0, pending restock date present | Recompute | Selects **Backorder** |
| B4 | FBA=0, total=0, **no** restock date | Recompute | Selects **Backorder** (total_stock<=0 branch), not Default |
| B5 | Only `default_strategy` populated, others empty | Recompute | Selects **Default** |
| B6 | All 4 strategy slots empty | Recompute | `active_strategy` = None → pricing carries "No active strategy" violation |
| B7 | FBA stock > 0 but `fba_strategy.is_active = 0` | Recompute | Falls through to next *active* strategy, not the disabled FBA |
| B8 | SFP eligible but supplier opted out of SFP (see test_sfp_backorder_item_optout) | Recompute | SFP skipped per opt-out rule; lands on Backorder/Default |

## C. Missing / broken strategies (user case #2)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| C1 | Pricing whose `active_strategy` points to a deleted Amazon Product Strategy | Run `collect_pricing_violations` | "Active strategy … does not exist" — blocked, no crash |
| C2 | `active_strategy` exists but `is_active = 0` | Validate/collect | "Active strategy … is disabled" |
| C3 | Active strategy has **no** purchase-cost row (rank-1 is_purchase_cost) | Collect | "No active purchase cost row on the active strategy" |
| C4 | Strategy exists but its `costs` table is empty | Save + collect | `calculated_min/max = 0`; pricing blocked on min/max ≤ 0 |
| C5 | Proxy SKU with an Amazon Product Pricing but the Proxy SKU has no `item` | Recompute strategy + stock | Stock treated as 0 (item-less guard); no exception |
| C6 | Delete a strategy that is currently the active_strategy, then push | Attempt push | Blocked with a clear violation; feed excludes this SKU |

## D. New SKU creation & publishing (user case #3)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| D1 | Create a fresh Item + UOM, generate a new Proxy SKU via `create_next_proxy_sku` | Inspect | Naming series `item-uom-####`; links item/uom/asin correctly |
| D2 | Generate Amazon Pricing for the new SKU (`generate_amazon_pricing_for_proxy_skus`) | Inspect | 1:1 Pricing created; strategy slots created from template |
| D3 | Apply default template (`apply_default_template_to_new_pricing`) | Inspect | Cost tree leaves populated; Auto rows resolve, Manual rows blank until set |
| D4 | New SKU with all costs still at default 0 | Attempt push | Push-blocked ("defaulted cost at 0" / min≤0), NOT published with a garbage price |
| D5 | New SKU fully configured, in-stock, Live mode | Build feed / dry-run | Appears in outbound payload with the exact computed min/max band + stock qty |
| D6 | New SKU created but never given a strategy | Build feed | Excluded with "No active strategy"; other SKUs in the batch still publish |
| D7 | Two Proxy SKUs sharing the same Item (pool_size = 2) | Build feed | Published sellable qty is divided by pool_size (no double-selling the same stock) |

## E. Stock update → strategy switch → push-guard (user case #4)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| E1 | SKU on FBA strategy (fba_stock 5). Drop FBA stock to 0 via stock entry, keep MFN 4 | Recompute | active_strategy flips FBA→SFP; band recomputed for SFP |
| E2 | SKU on SFP, drain MFN to 0, no restock | Recompute | flips SFP→Backorder |
| E3 | While flipping strategies, the newly-active strategy has an unsynced purchase cost (Auto, 0) | Recompute + collect | Push-block **fires** ("unsynced purchase cost") for the now-active strategy — the guard follows the active strategy |
| E4 | Stock update makes active strategy change to one whose `min <= purchase_cost` | Collect | Hard-floor violation fires on the new band; SKU not published below cost |
| E5 | Rapid consecutive stock updates (FBA 5→0→5) | Recompute twice | Ends on FBA again; no stale band; no orphaned Manual pin from the churn |
| E6 | Increase FBA stock so FBA strategy re-activates but FBA strategy is disabled | Recompute | Does not select disabled FBA; guard on fallback strategy still evaluated |

## F. Post-sales strategy change (user case #5)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| F1 | In-stock SKU on SFP; submit a Sales Order that consumes all free MFN stock | After SO submit, recompute | Stock drops → strategy flips to Backorder; band + push-guard re-evaluated on new strategy |
| F2 | MFN Amazon Sales Order submit triggers auto Material Request | Submit SO | Material Request created; restock/backorder signal reflected in `has_restock_date` |
| F3 | Sale reserves stock (Pick List auto stock reservation) reducing free qty | Create+submit Pick List | `get_free_qty_map` = actual − reserved; published qty drops accordingly (not the gross qty) |
| F4 | Sale then cancellation of the SO | Cancel SO | Stock/strategy revert; no double-count; band returns to pre-sale state |
| F5 | Sale that drops total to exactly 0 with a restock date on the item | Submit SO | Backorder selected via `has_restock_date` branch; restock_date threads into feed lead time |

## G. Navgold Settings — reservation & kill switches (user case #6)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| G1 | `stock_reserve_pct` unset/blank/None | Build feed | Treated as 0% reserve (`flt(None)`), published qty = full guarded qty; no crash |
| G2 | `stock_reserve_pct` = 25, qty 10 | Build feed | Published qty = floor(10 × 0.75) = 7 |
| G3 | `stock_reserve_pct` = 100 | Build feed | Published qty = 0 for every SKU (fully reserved) |
| G4 | `stock_reserve_pct` = 150 (out of range) or negative | Build feed | Clamped to [0,100]; no negative published qty |
| G5 | `max_publish_qty` = 3, actual guarded qty 20 | Build feed | Published qty capped at 3 |
| G6 | `freeze_amazon_outbound` = 1 | Attempt bulk push | `AmazonFrozenError`; records marked *Skipped* not *Failed*; nothing sent |
| G7 | `pricing_sync_mode` != "Live" (Dry Run) | Attempt push | `PricingNotApprovedError`; blocked; dry-run path still computes bands |
| G8 | `pricing_publishing_mode` = "Publish Selected Prices Only" with a subset list | Build feed | Only listed SKUs get a **price** patch; **[impl-eye]** confirm unselected SKUs still get their **stock** patch (must not suppress stock, only price) |
| G9 | `stock_publishing_mode` = "Publish Zero Stock for All Items" | Build feed | Every SKU's published qty = 0 regardless of real stock |
| G10 | `stock_publishing_mode` = "Publish Zero Stock for Selected Items" | Build feed | Only listed SKUs forced to 0; others publish real qty |
| G11 | `send_min_price_to_amazon` = 0 | Build feed | Min-price attribute omitted from payload; max/list price still present |

## H. Pack / bundle — not enough to make a pack (user case #7)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| H1 | Item free qty = 5, Proxy SKU UOM pack_size = 12 (can't make one pack) | Build feed | `calculate_adjusted_qty` = floor(5/pool/12) = 0; publishes 0, not a fractional/partial pack |
| H2 | Free qty = 25, pool_size 1, pack_size 12 | Build feed | Publishes 2 packs (floor(25/12)); remainder 1 not oversold |
| H3 | Product Bundle SKU where one component is short | Build feed | `get_buildable_bundle_qty_map` = min across components; limited by the scarce component |
| H4 | Bundle with a component missing a valuation/rate | Save/build | Logged via `log_missing_bundle_component_rates`; buildable qty computed defensively, no crash |
| H5 | pack_size = 0 or missing UOM conversion | Build feed | `calculate_adjusted_qty` guards (pack_size<=0 → 0); no division-by-zero |
| H6 | pool_size = 0 (no active Proxy SKU for item — edge) | Build feed | Guarded to 0; `pool_size or 1` fallback keeps math safe |
| H7 | Buildable qty rounds to 0 but strategy is SFP (needs stock>0) | Recompute | total_stock reflects 0 sellable → strategy does not stay SFP incorrectly |

## I. AFN / FBA stock not fetched (user case #8)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| I1 | AFN stock sync off / never run — AFN warehouse Bin empty | Recompute FBA SKU | `fba_stock` = 0 → FBA strategy not selected; falls to SFP/Backorder |
| I2 | AFN fetch API fails mid-run (simulate exception in `process_afn_stock_fetch`) | Run fetch | Error logged; existing AFN stock values left intact (not zeroed); pricing not corrupted |
| I3 | AFN fetch returns partial SKUs | Run fetch + recompute | Only fetched SKUs update; un-fetched keep prior value; no strategy flip from phantom 0 |
| I4 | FBA SKU with fba_stock from Amazon but AFN warehouse Bin also has qty | Recompute | Merge rule (max/Amazon-authoritative) applied consistently; no double count |
| I5 | FBA strategy active, fba_stock>0, but item has no AFN warehouse configured in SP API Settings | Recompute | Handled without crash; treated as 0 FBA stock |

## J. Cost-source / value_mode stickiness (the PR under review)
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| J1 | Auto row fed by Supplier-Sync; supplier price changes | Save strategy | Row re-derives to new supplier price (Auto follows feed) |
| J2 | Manually edit an Auto row's value via REST/`db.set_value`, then save strategy | Save | `pin_external_value_overrides` flips it to **Manual**; subsequent saves keep the pinned value |
| J3 | Manual row; supplier feed changes | Save | Manual value untouched (sticky) |
| J4 | Amazon-Sync (FBA fee) row written by the fee fetcher, then strategy saved | Save | Row NOT pinned to Manual, NOT overwritten by populate_costs (externally-owned) |
| J5 | Leaf with a `source` not in SOURCE_RESOLVERS (misconfig) | Save | Logged once via `frappe.log_error`; value left untouched; save does not abort the bulk run |
| J6 | Leaf with empty source + default_value 8 (self-contained), Auto | Save | Row value = 8 from default |
| J7 | Fed leaf whose resolver returns None (feed not ready) | Save | Value set to 0 (unsynced), not a stale prior value → triggers unsynced push-block |
| J8 | Bulk reset a cost type Auto→Manual with a fixed value, then reset back to Auto | Run bulk reset both ways | Auto→Manual pins value; Manual→Auto re-derives; FBA-fee value preserved on reset-to-Auto (the ponytail note) |
| J9 | Group node (is_group) accidentally given a source/default | Save Cost Type | `validate` blanks source/default on groups; fed-cost-has-no-default guard enforced on leaves |

## K. Outbound feed payload integrity
| ID | Setup | Action | Expected |
|----|-------|--------|----------|
| K1 | Fully valid in-stock SFP SKU, Live mode | Build feed | Payload ships the **exact** computed min/max band (byte/float-identical to `calculate_prices`) + guarded qty |
| K2 | Batch with one blocked SKU and several valid | Build feed | Blocked SKU excluded with reason; valid SKUs unaffected (no all-or-nothing abort) |
| K3 | FBA SKU with fba_stock>0 | Build feed | Fulfillment channel = AMAZON_EU, quantity omitted/0 (Amazon owns FBA stock) |
| K4 | Backorder SKU with restock date | Build feed | Lead-time / restock date present in payload; shipping group = backorder group UUID |
| K5 | Price deviation guard: new band deviates > threshold from last-sent | Build feed | SKU held back by deviation block (price_guard), reason recorded |
| K6 | `validate_pricing_feed` on a payload with a SKU missing a price patch | Validate | Flags the missing pricing patch when required; passes when stock-only allowed |

---

## Coverage map to your 8 asks
1. Price variations → **A**, **K1** 2. Missing strategies → **C**, **B6** 3. New SKU create+publish → **D** 4. Stock update → strategy switch + guard → **E** 5. Post-sales strategy change → **F** 6. Reservation not set in settings → **G1–G5** 7. Not enough to make a pack → **H** 8. AFN not fetched → **I** · plus cost-source stickiness (**J**) and payload integrity (**K**).

---

# RESULTS — run 2026-07-08 (navgold.localhost, 12 parallel agents, all uncommitted + rolled back)

**Headline:** 0 crashes, 0 data-corruption. ✅ No negative/zero/inverted price can reach Amazon; ✅ no below-cost publish; ✅ kill switches work. Failures below are correctness/operational gaps.

### A. Price-calculation variations — 10/10 PASS
| ID | Verdict | What the test actually said |
|----|---------|-----------------------------|
| A1 | PASS | Absolute 5, margins 10/30% → `min 5.56 / max 7.14` = `5/(1−margin)`, min≤max |
| A2 | PASS | Relative≥100% (denom−margin≤0) → `{0, 0}`, no crash/neg/inf |
| A3 | PASS | VAT 20% + cost 5 → `6.82 / 9.38`, both > cost |
| A4 | PASS | Threshold jump: cost 8.9 → `9.89` (no adj); cost 9.05 → `10.90` (0.07 adj applied above 10) |
| A5 | PASS | min_margin>max_margin → `validate_margin` throws "Min Margin cannot be greater than Max Margin" |
| A6 | PASS* | Negative cost −5 → `calculate_prices` returns `−7.14 / −5.56` (no floor at source!); caught downstream by `min<=0` + `purchase_cost<=0` guards → never publishes |
| A7 | PASS | Cost 1e9, tiny margin → `~1.076e9 / 1.077e9`, no overflow, min≤max |
| A8 | PASS | 2×Absolute + Relative + VAT → `7.89` = hand calc to 2dp, order-independent |
| A9 | PASS | margin == denominator (adjusted 0) → `{0,0}`, no div-by-zero |
| A10 | PASS | target_price below cost → `margin −100% / profit −10`, consistent, no crash |

### B. Strategy selection & switching — 8/8 PASS
| ID | Verdict | What it said |
|----|---------|--------------|
| B1 | PASS | fba_stock 3 → **FBA** |
| B2 | PASS | fba 0, total 5 → **SFP** |
| B3 | PASS | total 0, restock date → **Backorder** |
| B4 | PASS | total 0, no restock → **Backorder** (via `total<=0`), not Default |
| B5 | PASS | only default slot → **Default** |
| B6 | PASS | all 4 slots empty → **None** |
| B7 | PASS | fba_stock 3 but FBA disabled → falls through to **SFP** |
| B8 | PASS (design flag) | SFP opt-out asymmetric: `select_strategy` has no supplier-SFP concept; opt-out only gates the Backorder→SFP-shipping-group path, never an SFP-active SKU. **Plan wording, not code bug.** |

### C. Missing / broken strategies — 6/6 PASS
| ID | Verdict | What it said |
|----|---------|--------------|
| C1 | PASS | deleted active_strategy → "Active strategy … does not exist", no crash |
| C2 | PASS | disabled strategy → "Active strategy … is disabled" |
| C3 | PASS | no rank-1 purchase-cost row → "No active purchase cost row on the active strategy" |
| C4 | PASS | empty costs → `calc 0/0` → blocked on "Min/Max price is 0" |
| C5 | PASS | item-less Proxy SKU → all-zero stock, no exception |
| C6 | PASS | deleted-strategy SKU excluded from feed (in Stock+Prices mode). Note: Stock-Only mode still publishes stock (by design — you confirmed correct) |

### D. New SKU creation & publishing — 6 PASS, 1 FAIL
| ID | Verdict | What it said |
|----|---------|--------------|
| D1 | PASS | `create_next_proxy_sku` → correct naming series, deactivates old, links item/uom/asin |
| D2 | PASS | 1:1 pricing created (strategies are a separate template step) |
| D3 | PASS* | works after workaround; surfaced 2 Lows: no `is_default=1` template → silent no-op; weightless item → `resolve_shipping` throw aborts all 4 strategies |
| D4 | PASS | all-default-0 new SKU → push-blocked (3 violations), not published with garbage price |
| D5 | **FAIL** | fully-configured new SKU emits **0 feed messages** — `current_selling_price==0` → deviation guard "No current selling price". Chicken-and-egg deadlock (**BUG-4**) |
| D6 | PASS | batch with 1 strategy-less SKU → only valid SKUs publish, no all-or-nothing |
| D7 | PASS | 2 active Proxy SKUs on one item → each `floor(100/2)=50`, no double-sell |

### E. Stock update → strategy switch → push-guard — 6/6 PASS
| ID | Verdict | What it said |
|----|---------|--------------|
| E1 | PASS | fba 5→0 (mfn 4) → **FBA→SFP**, band re-derived |
| E2 | PASS | mfn drained → **SFP→Backorder** |
| E3 | PASS | post-flip strategy w/ unsynced Auto purchase cost → "unsynced purchase cost (FBA-Transfer)" fires on the *new* active strategy |
| E4 | PASS | post-flip `min<=purchase_cost` → hard-floor violation fires (24.27 ≤ 25) |
| E5 | PASS | fba churn 5→0→5 → ends FBA, no stale band |
| E6 | PASS | re-activated FBA but disabled → not selected, guard evaluates fallback |
| — | — | **Structural finding:** the guard can't evaluate a stale strategy — `active_strategy` is only ever set by re-running `select_strategy` in `validate()` |

### F. Post-sales strategy change — 4 PASS, 1 FAIL
| ID | Verdict | What it said |
|----|---------|--------------|
| F1 | PASS | SO submit reserves qty → total 10→0 → **SFP→Backorder** |
| F2 | **FAIL** | auto Material Request created, but `has_restock_date` stays False — `get_restock_data_map` reads submitted **POs** only, not MRs. Design-vs-plan (**#6**) |
| F3 | PASS | Pick List reservation → real SRE, `Bin.reserved_qty` no double-count, `free_qty=0` |
| F4 | PASS | SO cancel → reserved reverts, **Backorder→SFP**, no stale reservation |
| F5 | PASS | total→0 with PO restock date → **Backorder** via `has_restock_date` branch, restock_date threads to feed |

### G. Settings — reservation & kill switches — 8 PASS, 2 FAIL, 1 plan-fix
| ID | Verdict | What it said |
|----|---------|--------------|
| G1 | PASS | reserve None → 0%, `apply_stock_guard(10,None,0)=10` |
| G2 | PASS | reserve 25 → `floor(10×0.75)=7` |
| G3 | PASS | reserve 100 → 0 |
| G4 | PASS | reserve 150/−10 → clamped [0,100] (0 / 10) |
| G5 | PASS | max_publish_qty 3, qty 20 → capped 3 |
| G6 | PASS | `freeze_amazon_outbound` → `AmazonFrozenError` → records **Skipped** not Failed |
| G7 | PASS | Dry-Run → `PricingNotApprovedError`, blocked |
| G8 | **FAIL** | "Publish Selected Prices Only" → unselected SKU **absent from feed entirely** (loses stock patch too). Confirmed impl-eye bug (**BUG-3**) |
| G9 | **FAIL (Low)** | "Zero Stock for All" → inactive Proxy SKU published real qty 70, not 0 |
| G10 | PASS | "Zero Stock for Selected" → only listed SKU forced to 0 |
| G11 | plan-fix | `min` price always sent (safety floor); `send_min_price_to_amazon` actually gates `our_price`. Plan Expected is wrong |

### H. Pack / bundle — 7/7 PASS + 1 bug found while building fixtures
| ID | Verdict | What it said |
|----|---------|--------------|
| H1 | PASS | `calculate_adjusted_qty(5,1,12)=0` (can't make a pack) |
| H2 | PASS | `(25,1,12)=2`, remainder 1 not oversold |
| H3 | PASS | bundle buildable = `min(5,3)=3` (scarce component) |
| H4 | PASS | missing component rate → logged, returns 0, no crash |
| H5 | PASS | pack_size 0/−5 → 0, no div-by-zero |
| H6 | PASS | pool_size 0/−1 → 0 |
| H7 | PASS | 0 buildable → total_stock 0 → SFP not retained |
| — | **BUG** | `get_bundle_components` lacks `docstatus==1`/`is_active==1` → a draft bundle sharing `new_item_code` overrides the live recipe → oversell/misprice (**BUG-2**) |

### I. AFN / FBA stock not fetched — 3 PASS, 2 FAIL (same root)
| ID | Verdict | What it said |
|----|---------|--------------|
| I1 | PASS | empty AFN → fba_stock 0 → FBA not selected |
| I2 | **FAIL** | `fetch_for_skus` swallows per-batch/page errors silently (no log), returns partial dict (**BUG-1**) |
| I3 | **FAIL** | `compute_afn_stock_updates([{X:99}], {})` → `X:0` — un-fetched SKU phantom-zeroed, not preserved |
| I4 | PASS | `get_fba_stock_map` merges via `max(bin, amazon)`, no double count |
| I5 | PASS | no AFN warehouse configured → treated as 0, no crash |

### J. Cost-source Auto/Manual stickiness (the PR) — 8 PASS, 1 FAIL
| ID | Verdict | What it said |
|----|---------|--------------|
| J1 | PASS | Auto row re-derives to new supplier price |
| J2 | **FAIL** | external edit (999) → flips to Manual (correct) but persists stale in-memory value (20), reverting the edit (**BUG-5**) |
| J3 | PASS | Manual row untouched by feed |
| J4 | PASS | FBA-fee row (real fee-fetcher path) not pinned, not overwritten |
| J5 | PASS | unregistered source → logged once, value untouched, save not aborted |
| J6 | PASS | self-contained leaf default 8 → value 8 |
| J7 | PASS | resolver None → value 0 (not stale 17.5) |
| J8 | PASS | bulk reset Auto→Manual pins; Manual→Auto re-derives; FBA-fee preserved |
| J9 | PASS | group blanks source/default; fed leaf with non-zero default throws |

### K. Outbound feed payload integrity — 6/6 PASS
| ID | Verdict | What it said |
|----|---------|--------------|
| K1 | PASS | payload band `37.47/41.95` == live `calculate_prices` (float-identical) |
| K2 | PASS | 4-SKU batch: 2 blocked (VAT-zero, deviation), 2 published untouched — no abort |
| K3 | PASS | FBA → `fulfillment_channel_code AMAZON_EU`, quantity omitted |
| K4 | PASS | backorder → restock_date + `lead_time 3` + shipping group `a31ca016…` (main_revised) |
| K5 | PASS | deviation guard fires ("Price drop 25.9% exceeds 10%") |
| K6 | PASS | `validate_pricing_feed` flags missing price patch when required; passes stock-only |
| — | note | published band uses cached `current_min/max_price`; can drift if `on_update` bypassed (**#11**) |

### Negative-price probe (your #10) — NOT publishable (all vectors safe)
| Vector | Leak? | Blocked by |
|--------|-------|-----------|
| stale negative cache (−5/−2) | no | `collect_pricing_violations` `min/max<=0` → record dropped |
| zero boundary (0/0) | no | same (`<=0`) |
| inverted band (50/10) | no | `min>max` violation |
| post-validation backorder recompute, £−30 supplier | no | double-blocked: `recompute` returns None on `<=0` + `purchase_cost<=0` violation |
| positive control (£30) | publishes correctly | — |
- Root cause `calculate_prices` unsanitized (returns negatives) is **real but contained** by 3 downstream guards. Harden at source as defense-in-depth (**#10**).
- Side finding: `get_backorder_strategy_map` omits `cost_sub_type`/`value_mode` → live fallback-supplier override is **dead code** on that path (**#7-new**).

## Consolidated fix-list
- **High:** BUG-1 (AFN phantom-zero), BUG-2 (bundle docstatus), BUG-3 (selected-prices drops stock — unify with C6: price-blocks should skip only the price patch, keep stock), BUG-4 (new-SKU first-push deadlock — decision needed).
- **Medium:** BUG-5 (J2 stale value, in the PR), F2 (MR→restock signal, decision), dead fallback-supplier override.
- **Low:** G9 (inactive SKU zero-stock), D3 template throw/no-default, `calculate_prices` source sanitization, stale price cache.
- **Plan-wording (not code):** B8 (SFP opt-out scope), G11 (`send_min_price` gates `our_price`), C6 (Stock-Only keeps publishing stock — intended).
