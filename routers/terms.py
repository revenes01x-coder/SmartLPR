from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
from smartlpr.database import get_db
from smartlpr.security import get_current_user
from services import terms_service

router = APIRouter(prefix="/terms", tags=["Terms"])


@router.get("/latest")
def get_latest_terms():
    return terms_service.get_latest_terms()


@router.post("/accept")
async def accept_terms(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return await terms_service.accept_terms(db=db, current_user=current_user)
