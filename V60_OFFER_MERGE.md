# V60 — merged working offers + latest update

This build keeps the latest V59/V7 application features and restores the known-working V23 offer routing/delivery behavior.

Offer changes:
- `.офер` with no arguments always returns help instead of silently returning.
- Business and normal-private offer routing use the saved/active Business connection.
- Offer access/admin Business grant callbacks remain real DB actions.
- First offer does not embed the NFT URL as an HTML anchor in the offer text.
- NFT preview is sent as a separate URL-only message first, which keeps the Telegram-safe payload from V58 TEST 3 and makes the gift preview appear above the interactive offer.
- The offer itself has a clickable NFT URL button plus Accept/Decline buttons.
- Transfer profile now accepts a username without `@`, e.g. `witensea`, and stores `tg://send_gift?to=witensea`.
- Existing `tg://send_gift?to=...` and legacy `tg://user?id=...` profile links remain accepted.
- Final offer text also avoids HTML NFT anchors.
