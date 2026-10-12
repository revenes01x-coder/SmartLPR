from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from services.token import generate_api_key, hash_api_key
from services.rate_limiter import check_and_record
from services.audit_log import log_admin_action


# [Lockout ใหม่]: ตกลงกันไว้ = 3 ครั้ง / ล็อก 5 นาที ต่อ user
REGEN_API_KEY_LOCKOUT_LIMIT = 1
REGEN_API_KEY_LOCKOUT_MINUTES = 60


async def regenerate_api_key(
    ip_address: Optional[str],
    db: AsyncSession,
    current_user: models.User,
):
    await check_and_record(
        db,
        f"regen_api_key_{current_user.id}",
        "regen_api_key",
        limit=REGEN_API_KEY_LOCKOUT_LIMIT,
        window_minutes=REGEN_API_KEY_LOCKOUT_MINUTES,
    )

    plain_key = generate_api_key()
    current_user.api_key_hash = hash_api_key(plain_key)

    log_admin_action(
        db, current_user.id,
        action="api_key.regenerate",
        target_type="user",
        target_id=current_user.id,
        detail={},
        ip_address=ip_address,
        actor_type="user",
    )

    await db.commit()

    return schemas.ApiKeyResponse(api_key=plain_key)


def api_key_status(current_user: models.User):
    return schemas.ApiKeyStatusResponse(has_api_key=current_user.api_key_hash is not None)
