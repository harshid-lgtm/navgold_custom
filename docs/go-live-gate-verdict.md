# Go-Live Gate Verdict — go-live-prevention RC (2026-06-12)

Gate run per the go-live-gate process (financial invariants → pre-mortem → data gates → kill switch/rollback → canary → monitoring). Branch: `go-live-prevention` (develop + PRs #88–#95). Tests: 75/75.

| Phase | Verdict | Evidence |
|---|---|---|
| 1. Invariants enumerated | PASS | pricing_validators encodes all 8 client protection points; suite green |
| 2. Pre-mortem | PASS | audit reproduced the June-10 chain + found 7 new failure modes; all fixed (#94/#95) |
| 3. Data gates | **FAIL** | 71/300 sampled SKUs blocked (53 zero-cost, 28 min==max, 3 no cost row); ~2,224 zero-cost catalog-wide; 4,122 SKUs missing selling-price baselines |
| 4. Kill switch + rollback | PARTIAL | freeze_amazon_outbound proven (test + live flip); rollback matrix written; backup-at-cutover + known-good price CSV are cutover-day artifacts |
| 5. Canary | DESIGNED, NOT RUN | 14-SKU cost-source × strategy matrix in runbook; requires production + client |
| 6. Monitoring + humans | PARTIAL | 6 reports + poller + deviation alert live; missing: named 48h rota, scheduled Below-Min auto-run, two-person sign-off |

## Meta-gates
- PRs #88–#95 unreviewed/unmerged (review gate is deliberate).
- CI erpnext pin: apply manually (`bench get-app erpnext --branch version-16`, ci.yml:95) — push token lacks workflow scope.
- **Seller Central per-SKU min/max price bounds NOT set** — the external safety net; single highest-leverage remaining action.
- Production config (encryption key, flags) unverifiable from dev; cutover-day checklist in runbook §3.

## Verdict
**Code-ready: YES (pending review). Launch-ready: NO.** Remaining work is data (client), Amazon-side config, canary execution, and process — not engineering. Estimated: ~1 week data remediation + 1 week controlled canary/soak.
