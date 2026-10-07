# V7 Offer/Business fix

## What changed
- One-time local reset of offer subsystem: `gift_offers`, offer access grants, offer profiles, blocked markers and saved Business connection records are cleared. Unrelated parser/admin/bot data is preserved.
- New Business connections are **not** auto-granted offer access.
- Owner notification for a new Business connection now has a real `🟢 Выдать доступ к оферам` callback button.
- Admin Business-profile list now has real `Выдать доступ` / `Забрать доступ` callbacks.
- Normal private `.офер` first resolves the user's saved Business connection, then checks access. This fixes the old order that reported "подключи Business" even when a connection existed.
- A connected user without offer access receives: `Доступ к оферам не выдан. Обратитесь к владельцу бота для получения доступа.`
- A user without a saved Business connection receives the Business connection instruction.
- `.офер` without arguments continues to return the command instructions.

## One-time database reset
For the first V7 deployment set:

`OFFER_DB_RESET_V7=1`

The application performs the reset once and stores `offer_db_reset_v7_done=1` in `bot_settings`. Restarts will not wipe the database again.

The reset does NOT revoke Telegram Business on the user's account; it only clears the bot's local connection cache. Telegram will send a fresh BusinessConnection update when applicable.

After the first successful startup, the environment variable can be removed or set to `0`.

## Access flow
1. User connects Business -> bot saves the connection and notifies owner.
2. Owner presses `🟢 Выдать доступ к оферам`.
3. Bot writes the user ID into `offer_access_users` and clears local block state.
4. `.офер` works in the user's normal bot PM using that saved Business connection.
5. Without access, `.офер` returns the owner-access message instead of silently ignoring the command.
