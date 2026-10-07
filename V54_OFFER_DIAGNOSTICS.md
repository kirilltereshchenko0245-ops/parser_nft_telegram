# V54 — five isolated offer-send diagnostics

The production `.офер` flow is unchanged. This version adds an opt-in `.тестофер` command for an authorized Business user.

Tests:
1. plain text
2. text + callback inline button
3. NFT URL in text with link preview disabled
4. NFT URL + callback inline button with preview disabled
5. Telegraph URL button only (diagnostic payload; never added to production offers)

The diagnostic sender uses the originating `business_connection_id`, does not retry `PEER_FLOOD`, and logs every result. A compact summary is sent through the same Business route when possible.

The production offer remains free of Telegraph / `Как передать подарок` URL button.
