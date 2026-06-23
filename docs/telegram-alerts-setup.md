# Telegram Alerts — Setup & Reference

Real-time Telegram alerts + a periodic activity digest for the Amazon pricing/order
pipeline. **Send-only** — the bot posts alerts; it does not take commands (no
freeze-from-chat). All config lives on **Navgold Settings → Alerts & Safety**.

## One-time setup

1. **Create the bot** — message [@BotFather](https://t.me/BotFather) → `/newbot` →
   copy the token (looks like `8123456:AAH...`).
2. **Add the bot to your alerts group**, then post `/start@<your_bot_username>` in the
   group. The `@username` is required because the bot runs with privacy mode ON, so it
   only sees commands addressed to it.
3. In the desk: **Navgold Settings → Alerts & Safety**:
   - Tick **Enable Telegram Alerts**.
   - Paste the token into **Telegram Bot Token** and **Save** (the Detect/Test buttons
     only appear once a token is saved).
   - Click **Detect Chat ID** — it reads the bot's recent updates and fills **Telegram
     Chat ID**, preferring a group/supergroup/channel over a private DM.
   - Click **Send Test Alert** — a confirmation message should land in the group.

To move alerts to a different group later, repeat step 2 in the new group and click
**Detect Chat ID** again.

## Severity colour key

| Colour | Severity | Fires for | Push sound |
|---|---|---|---|
| 🔴 | critical | feed-submit failures, FBA-pull failures, below-min sales, circuit-breaker trip | yes |
| 🟡 | warning | stalled feeds, open failed syncs, stalled order sync, NO-GO digest | yes |
| 🟢 | ok | all-good digest, freeze released | silent |
| 🔵 | info | neutral notices | silent |

## What gets sent

- **Real-time problems** (instant, with ping): feed errors, FBA stock pull failures,
  below-min-price sales, and the loss circuit breaker tripping.
- **Activity digest** (every 30 min, silent): GO / NO-GO status, bleed rate, readiness,
  below-min count, feed errors, and a 30-minute rollup of orders synced, prices pushed,
  repricer pushed, and last FBA pull. Green 🟢 when all-good, yellow 🟡 when blocked.

## Loss circuit breaker (optional)

In the same tab, tick **Enable Loss Circuit Breaker** and set **Loss Circuit Breaker
Threshold**. If total below-min losses in a rolling 15-minute window cross the threshold,
all Amazon/Repricer outbound is auto-frozen and a 🔴 alert fires. Releasing the freeze is
a deliberate manual action (Navgold Settings, or the desk control center).

## Scheduling

`hooks.py` runs:
- `alerts.safety_sweep` every 15 min — circuit-breaker re-check + problem alerts.
- `alerts.send_digest` at `:05` and `:35` — the activity digest.

## Troubleshooting

- **Detect Chat ID → "No messages found"** — post a fresh `/start@<bot>` in the group;
  Telegram only retains recent updates (~24h).
- **Send Test → "Set the bot token first"** — the token didn't save; confirm it's in the
  Password field and the form was saved.
- **Nothing arrives, no error** — check **Enable Telegram Alerts** is ticked and the chat
  id is set. With both unset, alerting is a silent no-op by design.
- **Group alerts missing while DMs work** — privacy mode hides normal group messages;
  either address the bot with `/cmd@<bot>` or disable privacy via
  **@BotFather → /setprivacy → Disable**.

## Code map

- `navgold_custom/navgold_custom/amazon_pricing/alerts.py` — `notify`, `send_telegram`,
  `SEVERITY_TELEGRAM`, the `alert_*` hooks, `send_digest`, `get_activity_rollup`,
  `detect_chat_id`, `send_test`, and the circuit breaker.
- Config fields + buttons: `doctype/navgold_settings/navgold_settings.{json,js}`.
- Tests: `amazon_pricing/test_alerts.py`.
