
# V45 integration point

In the existing Business offer handler, temporarily import:

from offer_isolation_test import offer_isolation_test

Then call:

results = await offer_isolation_test(
    bot.send_message,
    chat_id=chat_id,
    text="DIAG OFFER TEST",
    business_connection_id=business_connection_id,
)

for name, ok, detail in results:
    logger.warning("OFFER ISOLATION TEST name=%s ok=%s detail=%s", name, ok, detail)

The four results must be sent through the SAME `bot.send_message` / Business connection used by `.офер`.

Do not use Telethon, alternate accounts, proxy rotation, peer spoofing, or automatic retries.
