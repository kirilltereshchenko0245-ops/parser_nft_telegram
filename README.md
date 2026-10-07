# V59 OFFER FIXED V5

Исправления:
- `.офер` без аргументов показывает инструкцию команд.
- `.офер` с неверным синтаксисом тоже показывает инструкцию.
- Business-офер определяется по точному `business_connection_id` входящего сообщения.
- Старый флаг `is_blocked` из V58/V59 больше не блокирует рабочее Business-подключение.
- Новое активное Business-подключение получает доступ к DEMO-оферам автоматически. Явная блокировка администратора сохраняется.
- При невозможности отправки из-за доступа бот сообщает причину, а не молча ничего не делает.
- NFT в первом офере остаётся URL-кнопкой, без HTML-якоря.

Примеры:
`.офер`
`.офер https://t.me/nft/PartySparkler-140864 1000 зв`
`.офер ру https://t.me/nft/PartySparkler-140864 1000 зв`
`.офер англ https://t.me/nft/PartySparkler-140864 1000 stars`


## V6 offer transport fix

The first `.офер` is deliberately sent as plain text with the NFT only in a URL button. Link previews are disabled. No HTML text-link, inline NFT URL, custom emoji, or preview is included in the first-offer payload. This follows the V58 isolation results: full text without the NFT anchor was stable, while production combinations containing the NFT link could return PEER_FLOOD.
