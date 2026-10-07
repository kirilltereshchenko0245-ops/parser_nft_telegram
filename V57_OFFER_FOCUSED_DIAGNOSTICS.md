# GiftParser V57 — focused first-offer diagnostics

V57 does not change production `.офер`.

` .тестофер ` now runs 10 tests. Tests 6–10 are sent without a diagnostic prefix so the production-like payload is not mutated.

6. Exact first-offer text, no buttons
7. Same text without the NFT hyperlink
8. Exact text + only `Отклонить` custom emoji button
9. Exact text + only `Принять` custom emoji button
10. Exact text + both custom emoji buttons

Preview is disabled for tests 6–10 to isolate payload/content from preview behavior.

Interpretation:
- 6 fails => text/content itself triggers PEER_FLOOD.
- 6 passes, 8/9 fail => custom emoji button is implicated.
- 8/9 pass, 10 fails => two-button combination is implicated.
- 6–10 pass => previous failure likely depended on preview/frequency/context rather than the payload itself.

No Telegram anti-spam restriction is bypassed.
