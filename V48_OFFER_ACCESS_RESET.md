# V48 — Offer access reset

Target requested for immediate reset/grant:
`8628184531`

The operation:
- removes matching rows from known offer/business access tables when the matching user-id column exists;
- recreates exactly one canonical `offer_access` row;
- grants access immediately;
- commits to SQLite;
- logs `OFFER ACCESS RESET+GRANTED user_id=8628184531`.

Important: this changes the bot's local database state. It does not and cannot forcibly disconnect/reconnect Telegram Business on the user's account.
