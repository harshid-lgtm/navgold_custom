# Plan — Cost Source Remodel

## Tracer-bullet slicing (build order)

A field rename is atomic — `source`→`value_mode` breaks every reader at once — so the slices are
dependency-ordered increments, each leaving the system **runnable + green** before the next.

- **Tracer 1 — Core pipeline (schema + migration + dispatch).** Sections 1, 2, 3, 8.
  Add `source` to Cost Sub Type, rename row `source`→`value_mode`, invert `populate_costs`,
  fix `price_calculator` + `fba_fee_fetcher`, write the migration. **Done = `bench migrate` clean +
  a price derives correctly for one fed (Supplier-Sync) and one defaulted (Commission) sub-type,
  Manual stays sticky.** This is the thinnest end-to-end proof of the whole model.
- **Tracer 2 — Admin surfaces.** Sections 4, 5, 6, 6a, 7.
  Templates seed `value_mode=Auto`; bulk-reset collapses to Auto/Manual; Purchase Cost Overview
  report + JS reworked; strategy/cost/sub-type client scripts. **Done = manage costs through the UI
  end-to-end on the new model.**
- **DEFERRED — Section 9 (push-guard blocking).** Explicitly out of scope this build per direction;
  separate follow-up.

Each tracer runs through frappe-feature (implement → test → review). Tracer 2 depends on Tracer 1's
schema, so they're sequential.

---


## Goal

Stop modeling `source` as a flat 8-value `Select` on every **Amazon Product Cost** row. Move
source-policy UP to **Cost Sub Type** (configure once), and make the row's source **derived
state** with only two values: `Auto` (a feed/default owns the value) or `Manual` (a human pinned
it). Kill the two bugs this causes:

- **Bad defaults** — `default_value` silently shipping for purchase/tax costs that must come from a feed.
- **Leaky dropdown** — all 8 sources offered on rows where only one is valid.

## The model (locked)

- **Each Cost Sub Type carries one `source`** — the single feed that owns its value (1:1, confirmed
  by every real example). `source` **empty = self-contained** (use `default_value`); `source`
  **set = fed** (use that feed). The empty/set state replaces a separate policy flag.
- The row's `value_mode` is **derived from user action**, not chosen: feed writes value → `Auto`;
  human edits value → `Manual`. `Manual` is **sticky** — feeds never overwrite it.
- `Auto` resolves by the sub-type's `source`:
  - `source` empty → value = sub-type `default_value` (overhead).
  - `source` set → value = that feed (purchase, tax, fee, shipping). No default; 0 until synced.

### Naming (locked)
| DocType | field | values | meaning |
|---|---|---|---|
| Cost Sub Type | `source` | (empty) / Supplier-Sync / PO-Sync / FBA-Transfer / Amazon-Sync / Item-Tax-Sync / Shipping-Sync | the feed that owns this cost; empty = self-contained |
| Amazon Product Cost | `value_mode` (renamed from `source`) | Auto / Manual | feed-owned vs hand-pinned |

---

## 1. Schema changes

### Cost Sub Type (`doctype/cost_sub_type/`)
Add:
| field | type | rules |
|---|---|---|
| `source` | Select (blank + 6 syncs: `\nSupplier-Sync\nPO-Sync\nFBA-Transfer\nAmazon-Sync\nItem-Tax-Sync\nShipping-Sync`) | optional; empty = self-contained, set = fed |

No separate `value_policy` field — the policy is encoded by whether `source` is set. Keep
`default_value` (meaningful only when `source` is empty).
Keep Select (not a Link to a new doctype) — the resolver registry is already keyed by these
strings; a Link is future option, not needed now.

**Remove `is_default` + `validate_single_default()`** — confirmed dead: nothing reads
`Cost Sub Type.is_default`, it was only self-guarded. Costs are now derived from templates that
name the exact sub_type, so there's no moment that needs a "default sub_type for this cost_type"
guess. Migration drops the column.

**validate():**
- `source` set ⇒ `default_value` must be 0/empty → else throw (a fed cost must not carry a default).
- `source` empty ⇒ `default_value` is the self-contained value (no constraint).

### Amazon Product Cost (`doctype/amazon_product_cost/amazon_product_cost.json`)
- **Rename `source` → `value_mode`**, options `Auto\nManual`, default `Auto`, read-only.
  Migration: `rename_field("Amazon Product Cost", "source", "value_mode")`.
- Update description. (`.py` Literal stub regenerates from JSON.)

### Cost Set Template Item (`doctype/amazon_cost_set_template_item/`)
- **Remove** the `source` Select (now derived from sub-type policy). Template seeds rows as `Auto`;
  the sub-type decides resolution. Update `.json` + `.py` stub.

---

## 2. Core dispatch inversion — `doctype/amazon_product_strategy/amazon_product_strategy.py`

This is the heart of the change (lines 53–209). Today it branches on `row.source`; invert to
branch on the **sub-type's policy**.

- Batch-fetch all referenced Cost Sub Type policies up front:
  `frappe.get_all("Cost Sub Type", fields=["name","source","default_value"])` → `policy_map` (no N+1).
- `populate_costs()` per row:
  - `value_mode == "Manual"` → leave value untouched (sticky).
  - else (`Auto`):
    - policy `source` empty → `value = policy.default_value`.
    - policy `source` set → `value = ITEM_SYNCED_SOURCE_RESOLVERS[policy.source](ctx)`; if the
      resolver returns `None` (feed not ready) → leave `value = 0`, mark unsynced.
- `ITEM_SYNCED_SOURCE_RESOLVERS` registry **stays as-is** (same 5 resolver fns, same string keys) —
  only the *dispatch key* changes from `row.source` to the sub-type's `source`.
- `synced_sources` set (line 150) and `get_auto_sync_defaults()` (196–209) fold into the policy
  loop; the `"Auto-Sync"` special-case disappears (it's just "source empty" now).
- Strategy-type constraints (FBA-Transfer only on FBA, Shipping excludes FBA) stay **inside the
  resolver fns** — unchanged.

`refresh_all_strategy_costs` (hooks daily) + `schedule_bulk_refresh_strategies` get the new behavior
for free — no signature change.

---

## 3. Calculators & feeders

- **`amazon_pricing/price_calculator.py:54`** — supplier override guard
  `if cost.source == "Supplier-Sync"` → `if policy.source == "Supplier-Sync" and cost.value_mode != "Manual"`.
  Pass `policy_map` into `get_cost_data()`.
- **`amazon_pricing/fba_fee_fetcher.py`** — ownership guard
  `:67` skip if `existing.value_mode == "Manual"` (not `!= FEE_SOURCE`); only write rows whose
  sub-type `source == "Amazon-Sync"`; `:90` append sets `value_mode = "Auto"`.
- **`amazon_pricing/pricing_validators.py:23,33,88`** — read-only; surface `value_mode`
  (optionally show the sub-type `source` for context).
- **`amazon_pricing/amazon_feed_builder.py:268`** — `source` field no longer exists; fetch
  `value_mode` instead (confirm it's unused downstream; likely droppable from the field list).

---

## 4. Templates — unify the two apply paths

Both currently disagree (`template_applicator` copies row source; `amazon_cost_set_template` +
`amazon_strategy_template` hardcode `"Manual"`). Unify all three to seed **`value_mode = "Auto"`**:

- `amazon_pricing/template_applicator.py:108`
- `doctype/amazon_cost_set_template/amazon_cost_set_template.py:59`
- `doctype/amazon_strategy_template/amazon_strategy_template.py:77`

Result: sub-types with empty `source` resolve to their `default_value` (same as before); fed
sub-types start at 0 and await their feed (the fix).

---

## 5. Bulk Purchase Cost Reset (`doctype/bulk_reset_purchase_cost_source/`)

This doctype hardcodes the exact map we're relocating — it simplifies massively.

- **Delete** `SUB_TYPE_TO_AUTO_SOURCE` + `DEFAULT_AUTO_SOURCE` (lines 28–36) and the whole
  `group_updates_by_target()` fan-out (187–203). The sub-type now owns the mapping.
- `target_source` Select → `Auto\nManual` only (`.json:73`); rename to `target_mode` for honesty.
- Reset to `Auto` → just write `value_mode = "Auto"` (sub-type source resolves on next refresh).
- Reset to `Manual` → write `value_mode = "Manual"` + `manual_value`.
- `current_source` filter Select → `Auto\nManual` (`.json:42`); rename to `current_mode`.
- `.js:15-33` confirm dialog copy updated.

---

## 6. Amazon Purchase Cost Overview report (`report/amazon_purchase_cost_overview/`)

The user-facing arbitrary-source editor — biggest UI rework.

- `.py:15` `ALLOWED_BULK_SOURCES` → `{"Auto","Manual"}`; `bulk_update_purchase_cost_source()`
  (`:174-210`) validates target ∈ {Auto, Manual}; writes `value_mode` + (for Manual) `value`.
- `.py:67-79` per-source columns → show the sub-type `source` as **info/read-only**, edit is
  Auto/Manual (`value_mode`).
- `.js` staged-edit grid (`:70-130, 233-255, 355-415`) → 2-option model; the FBA-Transfer-only-on-FBA
  validation (`:396-406`) is now enforced by sub-type config, drop from JS.
- `report/amazon_pricing_push_readiness/...py:132` `cost_source` column → show `value_mode`
  (+ sub-type `source` for context).

---

## 6a. Propagation semantics — how sub-type changes reach strategies

Cost rows store `value` **denormalized**; it is re-derived by `populate_costs()` at three moments:
strategy save, daily `refresh_all_strategy_costs`, and the 30-min cron `schedule_bulk_refresh_strategies`.
So sub-type policy changes propagate **lazily** — on the next refresh tick, not at sub-type save.

After refresh, per change: `default_value` → `Auto` rows on an empty-`source` sub-type pick it up;
`source` changed → `Auto` rows re-dispatch to the new feed; setting `source` on a
previously-default sub-type → `Auto` rows drop to 0 until the feed runs (**push guard blocks them**,
fails safe); `Manual` rows are **always sticky** (a policy change never overwrites a human override —
they stay islands until reset to `Auto`).

**Decision: propagation stays MANUAL — no auto-fire on sub-type save.**
- The existing **Navgold Settings → "Refresh All Costs"** button (`navgold_settings.js:106` →
  `refresh_all_strategy_costs`, chunked + enqueued on `long` queue) is the instant-propagation lever.
  Workflow: edit sub-type → click Refresh All Costs → push guard blocks any now-stale `Synced` row.
- Backstops if you forget: the 30-min cron auto-propagates; the push guard prevents shipping 0/stale.
- **Do NOT** enqueue refresh on Cost Sub Type `validate` — validate runs pre-write, can fire multiple
  times, and can fire on a rolled-back save. Rejected.
- `# ponytail:` full-fleet sweep — "Refresh All Costs" re-derives *every* active strategy, not just
  ones using the edited sub-type. Acceptable (enqueued + chunked). Upgrade path if it gets slow: a
  scoped refresh that queries Amazon Product Cost rows by `cost_sub_type` and refreshes only those
  parents — and only then is auto-fire-on-`on_update` worth considering.

---

## 7. JS

- `doctype/amazon_product_strategy/amazon_product_strategy.js:28-40` — Apply-template handler:
  `row.value_mode = "Auto"` (drop `cost.source`).
- **Add** `doctype/amazon_product_cost/amazon_product_cost.js` — on grid `value` change, flip
  `value_mode → "Manual"` (the derived-state behavior). Render `value_mode` read-only.
- **Add** `doctype/cost_sub_type/cost_sub_type.js` — when `source` is set, clear + hide
  `default_value` (belt-and-suspenders to the validate rule).

---

## 8. Migration patch (`patches.txt` + new patch module)

Order matters — backfill policy first, then rows.

1. **Rename the row field first:** `rename_field("Amazon Product Cost", "source", "value_mode")`
   (and the Cost Set Template Item field, before its `source` column is dropped).
2. **Cost Sub Type backfill** (set the new `source`):
   - VAT cost types → `source = Item-Tax-Sync`.
   - purchase sub-types (use the old `SUB_TYPE_TO_AUTO_SOURCE` map) → mapped `source`.
   - everything else (had a real `default_value`, no feed) → `source` empty (self-contained).
   - Log any sub-type whose historical rows used *two different* non-Manual sources (data smell —
     the 1:1 assumption broke; auto-pick most-frequent, report after).
3. **Amazon Product Cost rows (now `value_mode`):**
   - old `Manual` → `Manual`.
   - old `Auto-Sync` or any sync value → `Auto`.
4. Clear `default_value` on sub-types that got a `source` (so step-2 validation won't block later saves).

No `Date.now()`/random in patch; idempotent (re-running maps already-migrated values to themselves).

---

## 9. Guardrails (push-time invariant)

A fed cost (sub-type `source` set) with `value_mode = Auto` but `value == 0` (never synced) must
**block the push**, not ship silently. Extend:
- `report/amazon_go_live_data_readiness/` and `report/amazon_pricing_push_readiness/` — flag stale
  fed costs, naming the SKU + sub-type.
- Surface in the existing push guard so it refuses a price built on an unsynced purchase/tax cost.

---

## 10. Tests

Update: `test_pricing_validators.py`, `test_shipping_cost.py`, `test_bundle_rate_logging.py`,
`test_item_tax_sync.py`, `test_bulk_reset_source.py` (rewrite for Auto/Manual + policy dispatch).
Add: Cost Sub Type validation tests (Synced-requires-auto_source, Synced-forbids-default_value);
populate_costs dispatch test (Defaulted vs Synced); sticky-Manual test; unsynced-blocks-push test.

---

## Sequencing

1. Schema + validation (Cost Sub Type, Amazon Product Cost, template item) → `bench migrate`.
2. Migration patch (backfill) → verify on a console with real data, check the data-smell log.
3. Dispatch inversion in `amazon_product_strategy.py` + calculators/feeders.
4. Bulk reset + report rework.
5. Templates + JS.
6. Guardrails + tests → full run.

NO live Amazon testing — verify via `bench migrate`, console, and the test suite.

## Open decisions to confirm before coding

1. **Template item `source` — remove entirely (recommended) or keep as an optional Manual-seed override?**
   Removing is cleaner; keeping lets a template force a Manual default value on creation.
2. **Should the row `source` be hard read-only (action-derived only) or a visible 2-option toggle**
   the user *can* set? Recommend read-only + auto-flip on value edit.
3. **Migration data-smell rows** (a sub-type with 2+ historical sources) — block migrate and force
   manual cleanup, or auto-pick the most-frequent source and log? Recommend log + auto-pick, report after.
