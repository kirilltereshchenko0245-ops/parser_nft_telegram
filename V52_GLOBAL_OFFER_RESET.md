# V52 — global DEMO-offer reset + bootstrap access

- Added **🧹 Сбросить всех пользователей оферов** under Admin → 🎁 Доступ к оферам.
- Reset has a confirmation step and actually deletes local offer access, Business connection rows, block markers and per-user transfer profiles.
- Existing `gift_offers` history and unrelated bot/parser/admin data are preserved.
- Canonical owner is preserved.
- Telegram Business connections are not remotely disconnected; this is a local database reset.
- ID `8570516857` is explicitly bootstrapped with DEMO-offer access on startup and after the global reset. Telegram must still send its BusinessConnection update for a usable Business route.
- `.привет` Business handler remains present and sends through the exact originating Business connection.
