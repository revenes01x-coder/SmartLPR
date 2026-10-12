from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.schemas import normalize_user_contact_value
from services.audit_log import log_admin_action


async def _get_channel_or_404(db: AsyncSession, channel_id: int) -> models.ContactChannel:
    result = await db.execute(
        select(models.ContactChannel).filter(models.ContactChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบช่องทางติดต่อนี้")
    return channel


async def list_contact_channels(db: AsyncSession, current_user: models.User):
    result = await db.execute(
        select(models.ContactChannel)
        .order_by(models.ContactChannel.display_order.asc(), models.ContactChannel.id.asc())
    )
    return result.scalars().all()


async def create_contact_channel(
    payload: schemas.ContactChannelCreate,
    ip_address: Optional[str],
    db: AsyncSession,
    admin: models.User,
):
    max_order = (await db.execute(select(func.max(models.ContactChannel.display_order)))).scalar_one()

    new_channel = models.ContactChannel(
        label=payload.label,
        value=payload.value,
        icon=payload.icon,
        display_order=(max_order or 0) + 1,
    )
    db.add(new_channel)
    await db.flush()

    log_admin_action(
        db, admin.id,
        action="contact_channel.create",
        target_type="contact_channel",
        target_id=new_channel.id,
        detail={"label": new_channel.label, "value": new_channel.value, "icon": new_channel.icon},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(new_channel)
    return new_channel


async def update_contact_channel(
    channel_id: int,
    payload: schemas.ContactChannelUpdate,
    ip_address: Optional[str],
    db: AsyncSession,
    admin: models.User,
):
    channel = await _get_channel_or_404(db, channel_id)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ไม่มีข้อมูลที่จะแก้ไข",
        )

    if "value" in updates or "icon" in updates:
        resulting_icon = updates.get("icon", channel.icon)
        resulting_value = updates.get("value", channel.value)
        try:
            updates["value"] = normalize_user_contact_value(resulting_icon, resulting_value)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    for field, value in updates.items():
        setattr(channel, field, value)

    log_admin_action(
        db, admin.id,
        action="contact_channel.update",
        target_type="contact_channel",
        target_id=channel.id,
        detail=updates,
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(channel)
    return channel


async def delete_contact_channel(
    channel_id: int,
    ip_address: Optional[str],
    db: AsyncSession,
    admin: models.User,
):
    channel = await _get_channel_or_404(db, channel_id)

    log_admin_action(
        db, admin.id,
        action="contact_channel.delete",
        target_type="contact_channel",
        target_id=channel.id,
        detail={"label": channel.label, "value": channel.value, "icon": channel.icon},
        ip_address=ip_address,
    )

    await db.delete(channel)
    await db.commit()

    return {"message": f"ลบช่องทางติดต่อ '{channel.label}' เรียบร้อยแล้ว"}


async def reorder_contact_channel(
    channel_id: int,
    payload: schemas.ContactChannelReorderRequest,
    ip_address: Optional[str],
    db: AsyncSession,
    admin: models.User,
):
    channel = await _get_channel_or_404(db, channel_id)

    all_channels_result = await db.execute(
        select(models.ContactChannel)
        .order_by(models.ContactChannel.display_order.asc(), models.ContactChannel.id.asc())
    )
    all_channels = all_channels_result.scalars().all()

    idx = next(i for i, c in enumerate(all_channels) if c.id == channel.id)
    neighbor_idx = idx - 1 if payload.direction == "up" else idx + 1

    if 0 <= neighbor_idx < len(all_channels):
        neighbor = all_channels[neighbor_idx]
        channel.display_order, neighbor.display_order = neighbor.display_order, channel.display_order

        log_admin_action(
            db, admin.id,
            action="contact_channel.reorder",
            target_type="contact_channel",
            target_id=channel.id,
            detail={"direction": payload.direction, "swapped_with": neighbor.id},
            ip_address=ip_address,
        )

        await db.commit()

        result = await db.execute(
            select(models.ContactChannel)
            .order_by(models.ContactChannel.display_order.asc(), models.ContactChannel.id.asc())
        )
        all_channels = result.scalars().all()

    return all_channels
