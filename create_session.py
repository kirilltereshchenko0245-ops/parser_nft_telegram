import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("TG_API_ID", "2040"))
API_HASH = os.getenv("TG_API_HASH", "b18441a1ff607e10a989891a5462e627")

async def main():
    print("One-time setup: this utility runs outside the bot.")
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        await client.start()
        print("\nPARSER_SESSION_STRING=\n")
        print(client.session.save())
        print("\nCopy the value into Railway Variables. Never publish it.")

if __name__ == "__main__":
    asyncio.run(main())
