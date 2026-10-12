from typing import Optional
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import require_access_approved, get_current_user
from smartlpr.pagination import PageParams
from services import webhook_service

router = APIRouter(prefix="/webhook", tags=["Webhook Management"])


@router.post("/add", response_model=schemas.WebhookResponse)
async def add_webhook(
    webhook: schemas.WebhookCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_access_approved),
):
    return await webhook_service.add_webhook(
        webhook=webhook,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )


@router.get("/my", response_model=schemas.PaginatedResponse[schemas.WebhookResponse])
async def list_my_webhooks(
    order: Optional[str] = Query(default="desc", pattern="^(asc|desc)$"),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    # แค่ดูข้อมูล ไม่มีสิทธิ์สร้าง/แก้ -> ใช้ get_current_user เฉยๆ พอ ตาม dependency rule ข้อ 7
    current_user: models.User = Depends(get_current_user),
):
    """
    List webhook endpoint ทั้งหมดของตัวเอง พร้อมสถานะ circuit breaker
    (is_healthy, consecutive_dead_letters) — ใช้ทำ dashboard ดูภาพรวมว่า endpoint ไหน
    กำลังมีปัญหา/ถูกตัดไฟอยู่บ้าง
    """
    return await webhook_service.list_my_webhooks(
        order=order,
        page_params=page_params,
        db=db,
        current_user=current_user,
    )


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_access_approved),
):
    return await webhook_service.delete_webhook(
        webhook_id=webhook_id,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )
