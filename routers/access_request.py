from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import get_current_user, require_terms_accepted
from smartlpr.pagination import PageParams
from services import access_request_service

router = APIRouter(prefix="/access-request", tags=["Access Request"])


@router.post("/submit", response_model=schemas.AccessRequestResponse)
async def submit_access_request(
    payload: schemas.AccessRequestCreate,
    db: AsyncSession = Depends(get_db),
    # ต้องผ่าน require_terms_accepted ก่อนเสมอ (login -> terms -> access-request)
    current_user: models.User = Depends(require_terms_accepted),
):
    return await access_request_service.submit_access_request(
        payload=payload,
        db=db,
        current_user=current_user,
    )


@router.put("/update-pending", response_model=schemas.AccessRequestResponse)
async def update_pending_request(
    payload: schemas.AccessRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_terms_accepted),
):
    return await access_request_service.update_pending_request(
        payload=payload,
        db=db,
        current_user=current_user,
    )


@router.get("/my-status", response_model=schemas.PaginatedResponse[schemas.AccessRequestResponse])
async def my_access_requests(
    order: Optional[str] = Query(default="desc", pattern="^(asc|desc)$"),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return await access_request_service.my_access_requests(
        order=order,
        page_params=page_params,
        db=db,
        current_user=current_user,
    )
