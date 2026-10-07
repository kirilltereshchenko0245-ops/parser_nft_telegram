# V45 — Offer isolation diagnostics

This build isolates four Business send variants:

1. plain text — no keyboard, no preview URL
2. inline keyboard only
3. URL/preview only
4. inline keyboard + URL/preview

PEER_FLOOD is never retried automatically.

The helper is in `offer_isolation_test.py`:
`offer_isolation_test(send_func, chat_id=..., text=..., business_connection_id=...)`

Use it from the existing Business send path so the exact same bot/business connection is tested.
