from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import get_current_user
from services import my_contact_service

router = APIRouter(prefix="/my/contacts", tags=["My Contacts"])


@router.get("", response_model=list[schemas.UserContactResponse])
async def list_my_contacts(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """รายการข้อมูลติดต่อส่วนตัวทั้งหมดของตัวเอง (สูงสุด 1 รายการต่อ 1 ประเภท)"""
    return await my_contact_service.list_my_contacts(db=db, current_user=current_user)


@router.post("", response_model=schemas.UserContactResponse)
async def add_my_contact(
    payload: schemas.UserContactCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return await my_contact_service.add_my_contact(
        payload=payload,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )


@router.patch("/{contact_id}", response_model=schemas.UserContactResponse)
async def update_my_contact(
    contact_id: int,
    payload: schemas.UserContactUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """แก้ไข value ของข้อมูลติดต่อที่มีอยู่แล้วเท่านั้น (channel_type แก้ไม่ได้ — ต้องลบแล้ว
    เพิ่มใหม่ถ้าอยากเปลี่ยนประเภท) ใช้กฎ normalize/validate ตัวเดียวกับตอนสร้าง (เบอร์โทรต้องเป็น
    มือถือไทย 10 หลัก, อีเมลต้องมีรูปแบบถูกต้อง ฯลฯ) เพราะ UserContactUpdate ไม่มี channel_type
    ให้ pydantic validate เองตอน parse body (ต้องรู้ channel_type ของ record เดิมก่อน)"""
    return await my_contact_service.update_my_contact(
        contact_id=contact_id,
        payload=payload,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )


@router.delete("/{contact_id}")
async def delete_my_contact(
    contact_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return await my_contact_service.delete_my_contact(
        contact_id=contact_id,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )
