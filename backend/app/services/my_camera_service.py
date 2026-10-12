from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.pagination import PageParams


async def list_my_cameras(
    camera_id: Optional[str],
    order: Optional[str],
    page_params: PageParams,
    db: AsyncSession,
    current_user: models.User,
):
    base_query = (
        select(models.Camera, models.WebhookEndpoint.url, models.WebhookEndpoint.is_active)
        .join(models.WebhookEndpoint, models.Camera.webhook_endpoint_id == models.WebhookEndpoint.id)
        .filter(models.Camera.owner_user_id == current_user.id)
    )
    if camera_id:
        base_query = base_query.filter(models.Camera.id.ilike(f"%{camera_id.strip()}%"))

    if order == "asc":
        base_query = base_query.order_by(models.Camera.created_at.asc(), models.Camera.id.asc())
    else:
        base_query = base_query.order_by(models.Camera.created_at.desc(), models.Camera.id.desc())

    count_query = select(func.count()).select_from(base_query.order_by(None).subquery())
    total = (await db.execute(count_query)).scalar_one()

    rows_result = await db.execute(
        base_query
        .offset(page_params.offset)
        .limit(page_params.page_size)
    )
    rows = rows_result.all()

    total_pages = (total + page_params.page_size - 1) // page_params.page_size if total else 0

    return {
        "items": [
            schemas.MyCameraResponse(
                camera_id=c.id,
                is_active=c.is_active,
                verification_status=c.verification_status,
                delay=c.delay,
                webhook_url=webhook_url,
                webhook_is_active=webhook_is_active,
                created_at=c.created_at,
            )
            for c, webhook_url, webhook_is_active in rows
        ],
        "total": total,
        "page": page_params.page,
        "page_size": page_params.page_size,
        "total_pages": total_pages,
    }
