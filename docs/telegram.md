# Telegram event alerts

Almonds sends new live portfolio disclosures to your private Telegram chat. Earnings alerts are a short mobile brief: bold result highlights, a **Positive / Negative / Neutral** result label, a plain-language thesis, and at most two attributed management highlights from that reporting period. Revenue and net profit show available same-basis growth comparisons; reported EBITDA appears only when available. Internal evidence IDs, proxy calculations, research tables and empty report-link sections stay out of Telegram. The dashboard and complete research report are unchanged. See the [implementation, confidence rubric and limitations](intelligent-earnings-alerts.md).

Other disclosures (dividends, orders, corporate actions, management changes and other events) use three bold sections: **AI SUMMARY**, **IMPACT: Positive / Negative / Neutral**, and **WHAT MIGHT HAPPEN NEXT**. The impact includes a short evidence-backed explanation; future effects are conditional, not guaranteed price predictions. Event type alone never determines direction. Missing or mixed evidence stays neutral with an explanation, and unavailable AI is explicitly marked. The saved analysis retains its citations, while Telegram omits evidence IDs, research actions and page tables. A short source link is included when available. Existing web layouts remain unchanged.

## Connect your bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot`, and follow its name and username prompts. Keep the issued token private.
2. Open the new bot and send `/start` from the Telegram account that should receive alerts.
3. From the project root, run:

   ```sh
   backend/.venv/bin/python backend/scripts/setup_telegram.py --test
   ```

   Paste the bot token into the hidden prompt. Follow the prompt to send `/start`, then choose your private destination chat. The command verifies the bot, discovers the chat using `getUpdates`, writes `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to the project's `.env` with owner-only permissions, and sends the requested test. It preserves other configuration. Use a dedicated bot; it does not remove existing webhooks or consume messages with an update offset.

4. Apply the migration and restart the development API, or update the installed production runtime:

   ```sh
   cd frontend
   npm run build
   cd ..
   python3 ops/install-production.py
   ```

   The production launcher applies migrations through `0024_intelligent_earnings_alerts`. It copies the project `.env` into the installed snapshot, so restarting the old snapshot alone will not pick up project configuration changes. Docker Desktop and Ollama must be running; the Mac must be awake. For a development server, run `alembic upgrade head` from `backend` and restart it instead.

5. Open **Company events → Telegram alerts** to check delivery counts or send another connection test. `GET /api/telegram/status` gives the same secret-free status. `POST /api/telegram/test` explicitly sends a test to the configured destination.

These steps use Telegram's official [bot creation instructions](https://core.telegram.org/bots/features#creating-a-new-bot) and [Bot API](https://core.telegram.org/bots/api).

## Delivery behavior

- New live events for monitored portfolio companies enqueue one delivery in the same transaction as event storage. Repeated source observations do not enqueue another alert for the canonical event. Fixture/manual records and existing database events are not sent.
- By default, events published more than 48 hours ago are excluded, preventing historical archive imports from flooding the chat. Set `TELEGRAM_MAX_EVENT_AGE_HOURS` (1–720) before restarting if desired. This is based on the disclosed publication date, not the time the filing was first discovered.
- The independent worker sends one message part every five seconds when idle. AI generation runs in a separate thread so event ingestion and the API can continue. Earnings wait for validated financial ingestion; their DTO and formatted parts are persisted before sending and reused during retries.
- If Ollama is unavailable or output fails validation, earnings use the deterministic evidence-backed interpretation; the stored DTO retains `VERIFIED_FALLBACK` for auditing without displaying that internal status in the short brief. Non-earnings events state **AI summary unavailable** and explain that impact is unclear. Neither invents an AI conclusion. No later follow-up is sent automatically.
- Definite transient rejections and pre-connect failures retry with exponential backoff up to eight attempts; Telegram `retry_after` is respected. Permanent errors become `FAILED`. An explicit local retry endpoint preserves already acknowledged parts.
- Stable result/version/destination keys prevent duplicate queues. Per-part receipts survive restarts. Ambiguous timeouts or a restart during an in-flight send become `UNCERTAIN` and are not automatically replayed; inspect the chat before a deliberate new version.
- Full-report URLs require separately configured HTTPS routing and an independent signing secret. The short brief shows a link only when one is available; its stored DTO retains the explicit unavailable reason otherwise. Localhost links are never presented as phone-accessible reports. Known staleness, conflicting figures or unverified sources get a short caution instead of a data-quality table. See [remote report setup](intelligent-earnings-alerts.md#formatting-authentication-and-delivery).
- Set `TELEGRAM_ENABLED=false` and restart to pause enqueuing and delivery. Pending messages remain queued and resume when enabled. Keep the token in the private `.env`; it is never returned to the browser. Telegram transport errors are sanitized and HTTP request logs redact the bot token.

No public webhook, incoming Telegram commands, or Telegram channel ingestion is required. The endpoints follow Almonds's existing trusted-loopback deployment model.
