from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import require_api_key
from services import partner_service

router = APIRouter(prefix="/partner", tags=["Partner Integration"])


@router.post("/cameras", response_model=schemas.MyCameraResponse)
async def add_camera_from_partner(
    payload: schemas.PartnerCameraCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_api_key),
):
    return await partner_service.add_camera_from_partner(
        payload=payload,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )


@router.post("/cameras/status")
async def update_camera_status_from_partner(
    payload: schemas.PartnerCameraStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_api_key),
):
    return await partner_service.update_camera_status_from_partner(
        payload=payload,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )


@router.get("/cameras/{camera_id}", response_model=schemas.PartnerCameraStatusResponse)
async def get_camera_verification_status(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_api_key),
):
    return await partner_service.get_camera_verification_status(
        camera_id=camera_id,
        db=db,
        current_user=current_user,
    )


@router.delete("/cameras/{camera_id}")
async def delete_camera_from_partner(
    camera_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_api_key),
):
    return await partner_service.delete_camera_from_partner(
        camera_id=camera_id,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )
