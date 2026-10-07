# V56 — full offer payload diagnostics

Production `.офер` is unchanged. `.тестофер` now runs 8 isolated sends:
1. plain text
2. callback button
3. NFT URL without preview
4. NFT URL + callback button
5. Telegraph URL button alone
6. production-like first offer: exact text + custom emoji Accept/Decline buttons + large link preview
7. same first offer with preview disabled
8. production-like final accepted offer: exact text + Transfer URL button + large link preview

This is diagnostic only. It does not add Telegraph to production offers and does not perform any real transfer.
