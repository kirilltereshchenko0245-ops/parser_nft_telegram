# GiftParser V46 — real `.офер` Business send isolation

V46 attaches the four probes directly to the existing `_send_offer_message()` path.
It uses the same `bot.send_message()` call and the same `chat_id` / `business_connection_id`.

Enable temporarily in Railway:

`OFFER_DIAGNOSTICS=1`

Then run one `.офер` command.

The log will contain four lines:

`OFFER ISOLATION RESULT name=plain_text ...`
`OFFER ISOLATION RESULT name=keyboard_only ...`
`OFFER ISOLATION RESULT name=preview_only ...`
`OFFER ISOLATION RESULT name=keyboard_plus_preview ...`

Then:

`OFFER ISOLATION SUMMARY ...`

Important:
- No retries on PEER_FLOOD.
- This is a diagnostic harness only.
- It is disabled by default.
- The probes send clearly marked `DIAG OFFER ...` messages because Telegram does not provide a dry-run for sendMessage.
- After the test, set `OFFER_DIAGNOSTICS=0` or remove the variable.
