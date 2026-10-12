from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import get_current_user, require_access_approved
from services import api_key_service

router = APIRouter(prefix="/my/api-key", tags=["API Key"])


@router.post("/regenerate", response_model=schemas.ApiKeyResponse)
async def regenerate_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_access_approved),
):
    return await api_key_service.regenerate_api_key(
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )


@router.get("/status", response_model=schemas.ApiKeyStatusResponse)
def api_key_status(
    current_user: models.User = Depends(get_current_user),
):
    """เช็คว่ามี API key อยู่แล้วหรือยัง (ไม่โชว์ค่าจริง แค่บอกว่ามี/ไม่มี) ใช้ทำ UI เช่น
    ปุ่ม 'สร้าง API key' vs 'สร้างใหม่ (regenerate)'"""
    return api_key_service.api_key_status(current_user=current_user)
