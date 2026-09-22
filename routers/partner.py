import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import require_api_key
from security.camera_url_guard import verify_camera_rtsp_url
from services.rate_limiter import check_rate_limit
from services.audit_log import log_admin_action
from services.camera_storage import delete_camera_storage

router = APIRouter(prefix="/partner", tags=["Partner Integration"])

@router.post("/cameras", response_model=schemas.MyCameraResponse)
async def add_camera_from_partner(
    payload: schemas.PartnerCameraCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_api_key),
):

    await check_rate_limit(db, f"partner_add_camera_{current_user.id}", "partner_add_camera", limit=40, window_minutes=60)

    existing_id_result = await db.execute(select(models.Camera).filter(models.Camera.id == payload.camera_id))
    existing_id = existing_id_result.scalar_one_or_none()
    if existing_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"camera_id '{payload.camera_id}' ถูกใช้ไปแล้ว กรุณาตั้งชื่อ ID อื่น",
        )

    webhook_result = await db.execute(
        select(models.WebhookEndpoint).filter(
            models.WebhookEndpoint.url == payload.webhook_url,
            models.WebhookEndpoint.user_id == current_user.id,
        )
    )
    webhook = webhook_result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "ไม่พบ webhook_url นี้ในบัญชีของคุณ กรุณาสร้าง webhook ก่อนผ่าน POST /webhook/add "
                "แล้วใช้ URL เดียวกันมาผูกกับกล้อง"
            ),
        )

    if not payload.camera_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="จำเป็นต้องระบุลิงก์ RTSP (camera_url)",
        )
    
    rtsp_url = payload.camera_url

    # เช็ค rtsp_url ซ้ำ ว่าถูกเพิ่มเข้าระบบไปแล้วหรือยัง
    existing_url_result = await db.execute(select(models.Camera).filter(models.Camera.rtsp_url == rtsp_url))
    existing_url = existing_url_result.scalar_one_or_none()
    if existing_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ลิงก์กล้องไม่ถูกต้อง: ลิงก์นี้ถูกเพิ่มเข้าระบบไปแล้ว",
        )

    # [SSRF Guard]: ตรวจสอบความปลอดภัยของ RTSP url
    await asyncio.to_thread(verify_camera_rtsp_url, rtsp_url)

    new_camera = models.Camera(
        id=payload.camera_id,
        owner_user_id=current_user.id,
        rtsp_url=rtsp_url,
        webhook_endpoint_id=webhook.id,
        delay=payload.delay,
        is_active=False,
        verification_status="pending",
    )
    db.add(new_camera)
    
    log_admin_action(
        db, current_user.id,
        action="camera.add",
        target_type="camera",
        target_id=new_camera.id,
        detail={"webhook_endpoint_id": webhook.id},
        ip_address=request.client.host,
        actor_type="user",
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()

        recheck_result = await db.execute(select(models.Camera).filter(models.Camera.id == payload.camera_id))
        if recheck_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"camera_id '{payload.camera_id}' ถูกใช้ไปแล้ว กรุณาตั้งชื่ออื่น",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ลิงก์กล้องไม่ถูกต้อง: ลิงก์นี้ถูกเพิ่มเข้าระบบไปแล้ว",
        )

    await db.refresh(new_camera)

    return schemas.MyCameraResponse(
        camera_id=new_camera.id,
        is_active=new_camera.is_active,
        verification_status=new_camera.verification_status,
        delay=new_camera.delay,
        webhook_url=webhook.url,
        webhook_is_active=webhook.is_active,
        created_at=new_camera.created_at,
    )


@router.post("/cameras/status")
async def update_camera_status_from_partner(
    payload: schemas.PartnerCameraStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_api_key),
):

    await check_rate_limit(
        db, f"partner_camera_status_{current_user.id}", "partner_camera_status",
        limit=300, window_minutes=60,
    )

    result = await db.execute(
        select(models.Camera).filter(
            models.Camera.id == payload.camera_id,
            models.Camera.owner_user_id == current_user.id,
        )
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ไม่พบกล้อง camera_id='{payload.camera_id}' ในบัญชีของคุณ",
        )

    if payload.is_active is None and payload.delay is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="กรุณาระบุ is_active หรือ delay อย่างน้อย 1 รายการ",
        )

    action_details = {}
    messages = []

    if payload.is_active is not None:
        if payload.is_active and camera.verification_status != "verified":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"ไม่สามารถเปิดกล้องนี้ได้: กล้องยังไม่ผ่านการตรวจสอบ RTSP "
                    f"(สถานะปัจจุบัน: {camera.verification_status})"
                ),
            )
        camera.is_active = payload.is_active
        action_details["is_active"] = payload.is_active
        status_text = "เปิด" if payload.is_active else "ปิด"
        messages.append(f"{status_text}ใช้งานกล้อง")

    if payload.delay is not None:
        camera.delay = payload.delay
        action_details["delay"] = payload.delay
        messages.append(f"ปรับ delay เป็น {payload.delay} วินาที")

    log_admin_action(
        db, current_user.id,
        action="camera.status_change",
        target_type="camera",
        target_id=camera.id,
        detail=action_details,
        ip_address=request.client.host,
        actor_type="user",
    )

    await db.commit()

    return {
        "message": f"อัปเดตกล้อง '{camera.id}' เรียบร้อยแล้ว ({', '.join(messages)})",
        "camera_id": camera.id,
        "is_active": camera.is_active,
        "delay": camera.delay,
    }


@router.get("/cameras/{camera_id}", response_model=schemas.PartnerCameraStatusResponse)
async def get_camera_verification_status(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_api_key),
):

    await check_rate_limit(db, f"camera_status_check_{current_user.id}", "camera_status_check", limit=2400, window_minutes=60)

    result = await db.execute(
        select(models.Camera).filter(
            models.Camera.id == camera_id,
            models.Camera.owner_user_id == current_user.id,
        )
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"ไม่พบกล้อง camera_id='{camera_id}' ในบัญชีของคุณ อาจยังไม่เคยสร้างหรือถูกลบ"
                "ออกจากระบบแล้วเนื่องจากยืนยันการเชื่อมต่อ RTSP ไม่สำเร็จ กรุณาตรวจสอบลิงก์กล้อง"
                "แล้วลองสร้างใหม่อีกครั้ง"
            ),
        )

    return schemas.PartnerCameraStatusResponse(
        camera_id=camera.id,
        verification_status=camera.verification_status,
        is_active=camera.is_active,
        delay=camera.delay,
    )

@router.delete("/cameras/{camera_id}")
async def delete_camera_from_partner(
    camera_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(require_api_key),
):

    await check_rate_limit(
        db, f"partner_delete_camera_{current_user.id}", "partner_delete_camera",
        limit=40, window_minutes=60,
    )

    result = await db.execute(
        select(models.Camera).filter(
            models.Camera.id == camera_id,
            models.Camera.owner_user_id == current_user.id,
        )
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ไม่พบกล้อง camera_id='{camera_id}' ในบัญชีของคุณ",
        )

    # นับก่อนลบ เอาไว้บันทึกลง audit log (หลังลบไปแล้วนับไม่ได้อีก)
    event_count = (await db.execute(
        select(func.count()).select_from(models.WebhookEvent)
        .filter(models.WebhookEvent.camera_id == camera.id)
    )).scalar_one()

    if event_count:
        await db.execute(
            update(models.WebhookEvent)
            .where(models.WebhookEvent.camera_id == camera.id)
            .values(camera_id=None)
        )

    rtsp_url = camera.rtsp_url
    webhook_endpoint_id = camera.webhook_endpoint_id
    await db.delete(camera)  # Camera row หายจริง -> camera_id/rtsp_url นี้ reuse สร้างใหม่ได้ทันที
    delete_camera_storage(camera_id)  # ลบโฟลเดอร์รูป captures/camera_{id} และ logs/camera_{id}.log ทันที

    log_admin_action(
        db, current_user.id,
        action="camera.delete",
        target_type="camera",
        target_id=camera_id,
        detail={
            "rtsp_url": rtsp_url,
            "webhook_endpoint_id": webhook_endpoint_id,
            "orphaned_event_count": event_count,
            "storage_cleaned": True,
        },
        ip_address=request.client.host,
        actor_type="user",
    )

    await db.commit()

    return {
        "message": (
            f"ลบกล้อง '{camera_id}' ออกจากระบบเรียบร้อยแล้ว "
        )
    }