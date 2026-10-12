from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import get_current_user
from smartlpr.pagination import PageParams
from services import my_camera_service

router = APIRouter(prefix="/my", tags=["My Cameras"])


@router.get("/cameras", response_model=schemas.PaginatedResponse[schemas.MyCameraResponse])
async def list_my_cameras(
    camera_id: Optional[str] = Query(
        default=None,
        description="กรองเฉพาะกล้องที่ ID มีข้อความนี้อยู่ (partial, case-insensitive) — ไม่ระบุ = ดูทั้งหมด",
    ),
    order: Optional[str] = Query(
        default="desc",
        pattern="^(asc|desc)$",
        description="เรียงลำดับ: desc (ล่าสุด), asc (หลังสุด/เก่าสุด)",
    ),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return await my_camera_service.list_my_cameras(
        camera_id=camera_id,
        order=order,
        page_params=page_params,
        db=db,
        current_user=current_user,
    )
