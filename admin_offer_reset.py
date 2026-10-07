
"""V48 admin helper.

Use reset_and_grant_user_offer_access(db, target_user_id) from the admin callback.
"""
from offer_access_reset import reset_and_grant_offer_access

TARGET_USER_ID = 8628184531

async def reset_and_grant_user_offer_access(db, target_user_id: int = TARGET_USER_ID):
    return await reset_and_grant_offer_access(db, int(target_user_id))
