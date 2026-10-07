
"""V48: atomic offer access reset + grant for a Business user."""
import logging
logger = logging.getLogger(__name__)

async def reset_and_grant_offer_access(db, user_id: int):
    user_id = int(user_id)

    # Ensure canonical table exists.
    await db.execute("""
    CREATE TABLE IF NOT EXISTS offer_access (
        user_id INTEGER PRIMARY KEY,
        granted INTEGER NOT NULL DEFAULT 0,
        granted_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Remove duplicate/legacy rows from common offer access tables when present.
    # We only touch rows matching this exact Telegram user ID.
    tables = [
        "offer_access",
        "offer_users",
        "business_offer_access",
        "business_profiles",
    ]
    for table in tables:
        try:
            # First inspect columns so we don't make assumptions about schema.
            cur = await db.execute(f"PRAGMA table_info({table})")
            cols = {row[1] for row in await cur.fetchall()}
            for col in ("user_id", "telegram_id", "owner_id", "business_user_id"):
                if col in cols:
                    try:
                        await db.execute(f"DELETE FROM {table} WHERE {col}=?", (user_id,))
                    except Exception:
                        pass
                    break
        except Exception:
            pass

    # Recreate exactly one canonical permission row.
    await db.execute("""
        INSERT INTO offer_access(user_id, granted, granted_at)
        VALUES (?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            granted=1,
            granted_at=CURRENT_TIMESTAMP
    """, (user_id,))
    await db.commit()

    logger.warning(
        "OFFER ACCESS RESET+GRANTED user_id=%s canonical_row=1",
        user_id,
    )
    return True
