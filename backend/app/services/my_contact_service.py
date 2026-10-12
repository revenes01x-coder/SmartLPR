from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.schemas import normalize_user_contact_value
from services.audit_log import log_admin_action


async def _get_owned_contact_or_404(
    db: AsyncSession, contact_id: int, user_id: str
) -> models.UserContact:
    result = await db.execute(
        select(models.UserContact).filter(
            models.UserContact.id == contact_id,
            models.UserContact.user_id == user_id,
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ไม่พบข้อมูลติดต่อนี้ในบัญชีของคุณ",
        )
    return contact


async def list_my_contacts(db: AsyncSession, current_user: models.User):
    result = await db.execute(
        select(models.UserContact)
        .filter(models.UserContact.user_id == current_user.id)
        .order_by(models.UserContact.id.asc())
    )
    return result.scalars().all()


async def add_my_contact(
    payload: schemas.UserContactCreate,
    ip_address: Optional[str],
    db: AsyncSession,
    current_user: models.User,
):
    # เช็คก่อนว่ามีประเภทนี้อยู่แล้วหรือยัง (เร็ว อ่านง่าย) — ป้องกัน race condition จริงด้วย
    # unique constraint (user_id, channel_type) + จับ IntegrityError ด้านล่างอีกชั้น
    existing_result = await db.execute(
        select(models.UserContact).filter(
            models.UserContact.user_id == current_user.id,
            models.UserContact.channel_type == payload.channel_type,
        )
    )
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"คุณมีข้อมูลติดต่อประเภท '{payload.channel_type}' อยู่แล้ว "
                "กรุณาแก้ไขรายการเดิมแทนการเพิ่มใหม่"
            ),
        )

    new_contact = models.UserContact(
        user_id=current_user.id,
        channel_type=payload.channel_type,
        label=payload.label,
        value=payload.value,  # ผ่าน normalize_user_contact_value() ใน schema แล้ว (ดู model_validator)
    )
    db.add(new_contact)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"คุณมีข้อมูลติดต่อประเภท '{payload.channel_type}' อยู่แล้ว "
                "กรุณาแก้ไขรายการเดิมแทนการเพิ่มใหม่"
            ),
        )

    log_admin_action(
        db, current_user.id,
        action="contact_info.add",
        target_type="user_contact",
        target_id=new_contact.id,
        detail={"channel_type": new_contact.channel_type},
        ip_address=ip_address,
        actor_type="user",
    )

    await db.commit()
    await db.refresh(new_contact)
    return new_contact


async def update_my_contact(
    contact_id: int,
    payload: schemas.UserContactUpdate,
    ip_address: Optional[str],
    db: AsyncSession,
    current_user: models.User,
):
    contact = await _get_owned_contact_or_404(db, contact_id, current_user.id)

    try:
        normalized_value = normalize_user_contact_value(contact.channel_type, payload.value)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    contact.value = normalized_value
    
    if contact.channel_type == "generic":
        new_label = (payload.label or "").strip()
        if not new_label:
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ช่องทาง 'อื่นๆ' ต้องระบุชื่อช่องทางด้วย")
        contact.label = new_label
    else:
        contact.label = None

    log_admin_action(
        db, current_user.id,
        action="contact_info.update",
        target_type="user_contact",
        target_id=contact.id,
        detail={"channel_type": contact.channel_type},
        ip_address=ip_address,
        actor_type="user",
    )

    await db.commit()
    await db.refresh(contact)
    return contact


async def delete_my_contact(
    contact_id: int,
    ip_address: Optional[str],
    db: AsyncSession,
    current_user: models.User,
):
    contact = await _get_owned_contact_or_404(db, contact_id, current_user.id)

    log_admin_action(
        db, current_user.id,
        action="contact_info.delete",
        target_type="user_contact",
        target_id=contact.id,
        detail={"channel_type": contact.channel_type},
        ip_address=ip_address,
        actor_type="user",
    )

    await db.delete(contact)
    await db.commit()

    return {"message": "ลบข้อมูลติดต่อเรียบร้อยแล้ว"}
