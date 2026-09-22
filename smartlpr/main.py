import logging
from fastapi import FastAPI, Depends, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from smartlpr import models
from smartlpr.database import engine, get_db, SessionLocal
from routers import auth, webhook, terms, admin, access_request, my_cameras, api_key, partner, contact, my_contacts
from worker import start_scheduler
from services.camera_storage import prune_orphaned_camera_storage
import os
from smartlpr.config import CAPTURES_SAVE_DIR
from smartlpr.security import require_capture_event_secret
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

logger = logging.getLogger("smartlpr")


async def _init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
        from sqlalchemy import text
        await conn.execute(text("ALTER TABLE access_requests DROP COLUMN IF EXISTS contact_email;"))
        await conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS delay INTEGER NOT NULL DEFAULT 1;"))


async def _seed_contact_channels() -> None:
    """เพิ่มช่องทางติดต่อเริ่มต้น 3 รายการ (LINE / อีเมล / เวลาทำการ) เฉพาะตอนตาราง
    contact_channels ยังว่างอยู่ (deploy ครั้งแรก) รันครั้งเดียวตอน startup หลัง _init_models()
    สร้างตารางเสร็จ — ถ้ามีข้อมูลอยู่แล้วไม่ว่าเพราะเคย seed ไปแล้วหรือ admin ลบเองจนเหลือ 0 แถว
    พอดี จะไม่ seed ซ้ำ กันข้อมูลที่ admin ตั้งใจลบทิ้งกลับมาใหม่ทุกครั้งที่รีสตาร์ทเซิร์ฟเวอร์"""
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count(models.ContactChannel.id)))).scalar_one()
        if count:
            return

        db.add_all([
            models.ContactChannel(
                label="LINE Official Account", value="sp0803650401",
                icon="line", display_order=1,
            ),
            models.ContactChannel(
                label="อีเมล", value="saphonxch@gmail.com",
                icon="email", display_order=2,
            ),
            models.ContactChannel(
                label="เวลาทำการ", value="จันทร์–ศุกร์ 09:00–18:00 น.",
                icon="clock", display_order=3,
            ),
        ])
        await db.commit()


async def _cleanup_orphaned_camera_storage() -> None:
    """ลบโฟลเดอร์รูปภาพ captures/ และ logs/ ของกล้องที่ไม่มีอยู่ในฐานข้อมูลแล้ว (Orphaned Assets) ตอน startup"""
    async with SessionLocal() as db:
        await prune_orphaned_camera_storage(db)


async def _cleanup_orphaned_events() -> None:
    """Soft-delete webhook events ที่ไม่มี endpoint ผูกอยู่แล้ว (orphaned dead-letter / pending events) ตอน startup"""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        await db.execute(
            update(models.WebhookEvent)
            .where(
                models.WebhookEvent.webhook_endpoint_id.is_(None),
                models.WebhookEvent.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        await db.commit()


# Lifespan: สร้างตาราง (ถ้ายังไม่มี) + seed ช่องทางติดต่อเริ่มต้น (ถ้าตารางว่าง) + สั่งให้ Worker
# ทำงานตอนเปิดเซิร์ฟเวอร์ และปิด Worker ตอนปิดเซิร์ฟเวอร์
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(" เริ่มระบบ SmartLPR Webhook API และ Background Worker...")
    await _init_models()
    await _seed_contact_channels()
    await _cleanup_orphaned_camera_storage()
    await _cleanup_orphaned_events()
    scheduler = start_scheduler()
    yield
    print(" กำลังปิดระบบและหยุดการทำงานของ Worker...")
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan, title="SmartLPR Webhook System")

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error: {request.method} {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={"detail": "เกิดข้อผิดพลาดที่ไม่คาดคิดในระบบ กรุณาลองใหม่อีกครั้ง หากยังพบปัญหากรุณาติดต่อผู้ดูแลระบบ"},
    )

# ---------------------------------------------------------------------------
# CORS — อนุญาตให้หน้า static (index.html ที่รันแยกด้วย `python -m http.server`
# บน origin คนละพอร์ตกับ backend นี้) เรียก fetch() เข้ามาได้
# ถ้า deploy หน้าเว็บที่ domain/พอร์ตอื่น ต้องมาแก้ allow_origins ให้ตรงด้วย
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "null",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# นำ Endpoint จากไฟล์ routers มาเสียบเข้ากับแอปหลัก
app.include_router(auth.router)
app.include_router(terms.router)
app.include_router(access_request.router)
app.include_router(webhook.router)
app.include_router(api_key.router)
app.include_router(my_cameras.router)
app.include_router(admin.router)
app.include_router(partner.router)
app.include_router(contact.router)
app.include_router(my_contacts.router)

def _is_valid_capture_path(path: str, camera_id: str, kind: str) -> bool:
    """บังคับ path รูปต้องอยู่ใต้ captures/camera_{id}/{kind}/ เท่านั้น (รูปแบบเดียวกับที่
    camera_worker.py สร้างไฟล์จริง) กันคนตั้ง path เป็นไฟล์อื่นบน server แล้วให้ worker.py
    เปิดไฟล์นั้นแนบส่งออกทาง webhook ของตัวเองทีหลัง (arbitrary file read)"""
    expected_root = os.path.realpath(os.path.join(CAPTURES_SAVE_DIR, f"camera_{camera_id}", kind))
    resolved = os.path.realpath(path)
    return resolved == expected_root or resolved.startswith(expected_root + os.sep)


@app.post("/capture-event", tags=["Internal System"])
async def receive_from_rtsp(
    camera_id: str = Form(...),
    event_id: str = Form(...),
    plate: str = Form(...),
    province: str = Form(""),
    color: str = Form(""),
    timestamp: str = Form(...),
    full_image_path: str = Form(...),
    crop_image_path: str = Form(...),
    is_update: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_capture_event_secret),
):

    # 1. เช็คว่า camera_id นี้มีจริงและ is_active=True ไหม ถ้าไม่ผ่าน -> ignore
    camera_result = await db.execute(select(models.Camera).filter(models.Camera.id == camera_id))
    camera = camera_result.scalar_one_or_none()
    if not camera or not camera.is_active:
        return {"status": "ignored", "message": "ไม่พบกล้องนี้ในระบบ หรือกล้องถูกปิดใช้งานอยู่"}

    if not _is_valid_capture_path(full_image_path, camera_id, "full") or \
       not _is_valid_capture_path(crop_image_path, camera_id, "crop"):
        return {"status": "ignored", "message": "Pathไฟล์รูปไม่ถูกต้อง"}

    # 2. เจ้าของกล้องคือ owner_user_id ตรงๆ (กล้องเป็นกรรมสิทธิ์ของ user คนเดียว )
    owner_user_id = camera.owner_user_id

    # 2.5 [Suspend Guard]: เจ้าของกล้องถูกระงับอยู่ -> ไม่สร้าง WebhookEvent ใหม่เข้าคิวเลย
    owner_result = await db.execute(select(models.User).filter(models.User.id == owner_user_id))
    owner = owner_result.scalar_one_or_none()
    if not owner or owner.is_suspended:
        return {"status": "ignored", "message": "บัญชีเจ้าของกล้องนี้ถูกระงับการใช้งานอยู่ ไม่ส่งข้อมูลต่อ"}

    # 3. เก็บข้อมูลข้อความไว้ใน payload (path รูปเก็บแยกคนละ column)

    text_payload = {
        "event_id": event_id,
        "camera_id": camera_id,
        "license_plate": plate,
        "province": province,
        "color": color,
        "capture_time": timestamp,
        "is_update": is_update,
    }

    if is_update:
        # [Confidence Upgrade]: อัปเดตข้อมูลของเหตุการณ์เดิมที่เพิ่งบันทึกไป
        existing_event_result = await db.execute(
            select(models.WebhookEvent).filter(
                models.WebhookEvent.source_event_id == event_id,
                models.WebhookEvent.deleted_at.is_(None),
            )
        )
        existing_event = existing_event_result.scalar_one_or_none()
        if existing_event:
            existing_event.payload = text_payload
            existing_event.full_image_path = full_image_path
            existing_event.crop_image_path = crop_image_path
            existing_event.status = "pending"
            existing_event.attempt_count = 0
            existing_event.next_retry_at = datetime.now(timezone.utc)
            await db.commit()
            return {"status": "updated", "message": "อัปเดตข้อมูลป้ายทะเบียนเรียบร้อย"}

    # 4. หา WebhookEndpoint ที่กล้องตัวนี้ผูกไว้
    #    WebhookEvent ลงคิว 1 event ต่อ 1 กล้อง/1 การตรวจจับเท่านั้น
    endpoint_result = await db.execute(
        select(models.WebhookEndpoint).filter(
            models.WebhookEndpoint.id == camera.webhook_endpoint_id,
            models.WebhookEndpoint.is_active == True,  # noqa: E712
        )
    )
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        return {"status": "ignored", "message": "Webhook ที่กล้องนี้ผูกไว้ถูกปิดใช้งานอยู่"}

    new_event = models.WebhookEvent(
        id=f"{event_id}_{endpoint.id}",  # ทำให้ ID ไม่ซ้ำ (primary key)
        source_event_id=event_id,        # event_id ต้นฉบับ ใช้ verify ACK
        user_id=owner_user_id,
        camera_id=camera_id,
        webhook_endpoint_id=endpoint.id,
        target_url=endpoint.url,
        payload=text_payload,
        full_image_path=full_image_path,
        crop_image_path=crop_image_path,
        status="pending",
        attempt_count=0,
        next_retry_at=datetime.now(timezone.utc)  # สั่งให้ทำทันที
    )
    db.add(new_event)

    try:
        await db.commit()
    except IntegrityError:
        # กันกรณีกล้องยิง event_id ซ้ำ (เช่น retry เอง) ไม่ให้ 500
        await db.rollback()
        return {"status": "ignored", "message": "event_id นี้เคยถูกบันทึกไปแล้ว"}

    return {"status": "success", "message": "เพิ่มข้อมูลลงคิวเรียบร้อย Worker จะจัดการส่งต่อให้ทันที"}


# ---------------------------------------------------------------------------
# [Proxy Headers]: ต้องอยู่ท้ายไฟล์นี้เสมอ — หลังจากลงทะเบียน route/middleware ทั้งหมดกับ
# FastAPI app (exception_handler, CORSMiddleware, include_router ทุกตัว) เสร็จเรียบร้อยแล้ว
# เท่านั้น ProxyHeadersMiddleware เป็นแค่ ASGI wrapper ธรรมดา (ไม่มี .include_router/
# .add_middleware/.exception_handler เหมือน FastAPI) ถ้าห่อไว้เร็วเกินไปโค้ดที่เรียก method
# พวกนี้กับ app หลังจากนั้นจะพังทันที (AttributeError) — uvicorn (smartlpr.main:app) จะหยิบ
# ตัวแปร app ตัวสุดท้ายในไฟล์นี้ไปใช้เป็น ASGI entrypoint จริง
# ---------------------------------------------------------------------------
app = ProxyHeadersMiddleware(app, trusted_hosts="*")