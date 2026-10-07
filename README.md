# V43 — Offer parser fixed

Изменения:
- Исправлен regex подарка: `https://t.me/nft/Name-123`, `t.me/nft/Name-123`,
  URL с `/`, `?query` и `#fragment` распознаются.
- Перед разбором удаляются zero-width символы, backticks и типографские кавычки.
- Ошибка отсутствующей ссылки теперь показывает ожидаемый формат.
- Отдельные `.тестофер`/`.тестофер2` удалены из диагностической версии.
- Business-отправка DEMO-офера остаётся прежней; реальных переводов Stars/подарков нет.

Тест:
`.офер https://t.me/nft/PartySparkler-140864 150 Gram`


## V44 offer diagnostics
V44 records the exact DEMO-offer send stage in `logs/giftparser.log`. For an offer send, look for:
- `OFFER SEND API START method=sendMessage`
- `OFFER SEND API OK ...`
- or `OFFER SEND API BAD_REQUEST ... PEER_FLOOD`

The offer is sent only through the originating Business connection. There is no normal-bot fallback and no anti-spam bypass.
