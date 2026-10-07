# V50 — admin Business session reset

Added to **Админка → Управление пользователями → пользователь**:

- `♻️ Сбросить Business-сессии`

The action removes all locally stored `offer_business_connections` rows for the selected Telegram user, clears the local blocked marker, clears the in-memory Business caches, and preserves the user's offer entitlement/access. The next Business update can register a clean current connection.

Also changed **Админка → Доступ к оферам → Business-профили** to display only one row per user, so duplicate connection records no longer produce three identical buttons.

Important: this is a local bot-side reset. It does not forcibly disconnect Telegram Business on the user's account.
