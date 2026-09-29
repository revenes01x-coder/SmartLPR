from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import get_current_user, require_admin
from services import contact_service

router = APIRouter(tags=["Contact"])


@router.get("/contact", response_model=list[schemas.ContactChannelResponse])
async def list_contact_channels(
    db: AsyncSession = Depends(get_db),
    # ใช้ get_current_user เฉยๆ (ไม่ผ่าน require_terms_accepted/require_access_approved) —
    # ตั้งใจให้ user ที่บัญชีถูกระงับหรือคำขอใช้งานยังไม่อนุมัติ เข้าดูช่องทางติดต่อทีมงานได้เสมอ
    current_user: models.User = Depends(get_current_user),
):
    """รายการช่องทางติดต่อทั้งหมด เรียงตาม display_order"""
    return await contact_service.list_contact_channels(db=db, current_user=current_user)


@router.post("/admin/contact-channels", response_model=schemas.ContactChannelResponse)
async def create_contact_channel(
    payload: schemas.ContactChannelCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """เพิ่มช่องทางติดต่อใหม่ — ต่อท้ายลิสต์เสมอ (display_order = ค่าสูงสุดปัจจุบัน + 1)
    admin ใช้ปุ่มเลื่อนขึ้น/ลง (ดู reorder_contact_channel ด้านล่าง) จัดลำดับใหม่เองทีหลังได้

    [Format Guard]: payload.value ถูกตรวจ/normalize ตาม payload.icon ไปแล้วตั้งแต่ระดับ schema
    (ดู schemas.py: ContactChannelCreate.normalize_value — ใช้ normalize_user_contact_value
    ฟังก์ชันเดียวกับ /my/contacts) ไม่ต้องเช็คซ้ำในนี้อีก"""
    return await contact_service.create_contact_channel(
        payload=payload,
        ip_address=request.client.host,
        db=db,
        admin=admin,
    )


@router.patch("/admin/contact-channels/{channel_id}", response_model=schemas.ContactChannelResponse)
async def update_contact_channel(
    channel_id: int,
    payload: schemas.ContactChannelUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """แก้ไขบางฟิลด์ (partial update) — ฟิลด์ที่ไม่ได้ส่งมาใน body จะไม่ถูกแตะเลย
    (exclude_unset=True)

    [Format Guard]: ต่างจาก create ตรงที่ schema (ContactChannelUpdate) validate รูปแบบ value
    ตาม icon เองไม่ได้ทั้งหมด เพราะเป็น partial update (ส่งมาแค่ value อย่างเดียว หรือแค่ icon
    อย่างเดียวก็ได้) จุดนี้จึงเป็นคนรวม icon/value "ผลลัพธ์สุดท้าย" หลัง merge กับ record เดิมก่อน
    ค่อยเรียก normalize_user_contact_value เช็ค/normalize ให้ — กันเคสแก้แค่ icon จาก 'generic'
    เป็น 'phone' แต่ value เดิมไม่ใช่รูปแบบเบอร์โทรหลุดผ่านไปได้ (หรือกลับกัน แก้แค่ value ให้เป็น
    ข้อความสั้นๆ ทั้งที่ icon เดิมเป็น 'phone' อยู่แล้ว)"""
    return await contact_service.update_contact_channel(
        channel_id=channel_id,
        payload=payload,
        ip_address=request.client.host,
        db=db,
        admin=admin,
    )


@router.delete("/admin/contact-channels/{channel_id}")
async def delete_contact_channel(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await contact_service.delete_contact_channel(
        channel_id=channel_id,
        ip_address=request.client.host,
        db=db,
        admin=admin,
    )


@router.post(
    "/admin/contact-channels/{channel_id}/reorder",
    response_model=list[schemas.ContactChannelResponse],
)
async def reorder_contact_channel(
    channel_id: int,
    payload: schemas.ContactChannelReorderRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """สลับ display_order ของช่องทางนี้กับตัวที่อยู่ติดกันในทิศทางที่ระบุ (ขึ้น/ลง) แล้วคืนลิสต์
    เต็มที่เรียงใหม่แล้วกลับไปเลย (frontend เอาไปวาดใหม่ตรงๆ ไม่ต้อง GET /contact ซ้ำ)

    ถ้าอยู่บนสุด/ล่างสุดอยู่แล้ว (ไม่มีตัวข้างเคียงให้สลับในทิศทางนั้น) ไม่ error แค่ไม่ทำอะไร
    แล้วคืนลิสต์เดิมกลับไปเฉยๆ — ปุ่มขึ้น/ลงฝั่ง frontend ก็ disable ไว้ล่วงหน้าอยู่แล้วในเคสนี้"""
    return await contact_service.reorder_contact_channel(
        channel_id=channel_id,
        payload=payload,
        ip_address=request.client.host,
        db=db,
        admin=admin,
    )
