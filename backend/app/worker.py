import os
import asyncio
import uuid
import logging
import httpx
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
from smartlpr.database import SessionLocal
from services.email_service import send_webhook_endpoint_unhealthy_email
from services.audit_log import log_admin_action
from services.camera_storage import delete_camera_storage, prune_orphaned_camera_storage
from services.camera_verifier import check_camera_rtsp
from smartlpr.config import (UNVERIFIED_USER_EXPIRE_HOURS, PLATE_DATA_RETENTION_DAYS, OTP_RETENTION_DAYS, ADMIN_AUDIT_LOG_RETENTION_DAYS)
from security.ssrf_guard import build_pinned_request, build_test_webhook_payload
from security.ip_guard import SSRFBlockedError

RETRY_DELAYS = {1: 3, 2: 5, 3: 10}
DEAD_LETTER_THRESHOLD = 3

# เพดานการยิง webhook พร้อมกัน "ต่อ 1 endpoint" (แต่ละ endpoint มี Semaphore ของตัวเอง) — กันเซิร์ฟเวอร์
# ลูกค้ารายเดียวโดนถล่ม และกัน endpoint ที่มี event เยอะบังคิว endpoint อื่น
# ตัวส่งปกติมีเพดานรวมทั้งระบบอีกชั้น (WEBHOOK_MAX_IN_FLIGHT ด้านล่าง) ส่วน graveyard ใช้ RESUME_PER_ENDPOINT_CONCURRENCY
REALTIME_PER_ENDPOINT_CONCURRENCY = 5
RESUME_PER_ENDPOINT_CONCURRENCY = 3

HEALTH_CHECK_TIMEOUT_SECONDS = 5

# Camera RTSP verification: timeout ต่อกล้อง 1 ตัว (วินาที) ตอนลอง connect+อ่านเฟรมจริง
CAMERA_VERIFY_TIMEOUT_SECONDS = 10
CAMERA_VERIFY_MAX_ATTEMPTS = 3

CAMERA_VERIFY_CONCURRENCY = 5


def _is_valid_ack(event: models.WebhookEvent, response: httpx.Response) -> bool:
    """
    ACK ถูกต้องก็ต่อเมื่อ status 2xx + body เป็น JSON parse ได้ + event_id ตรงกับ source_event_id
    (ไม่แตะ DB เลย ไม่ต้องเป็น async)
"""
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False

    received_event_id = body.get("event_id")
    if not isinstance(received_event_id, str):
        return False

    try:
        return uuid.UUID(received_event_id) == uuid.UUID(event.source_event_id)
    except (ValueError, AttributeError, TypeError):
        # source_event_id หรือค่าที่ปลายทางตอบมา parse เป็น UUID ไม่ได้ (ผิดปกติ) -> ถือว่าไม่ตรง
        return False


def _read_event_images(event: models.WebhookEvent) -> dict:
    """อ่านไฟล์รูป full/crop แบบ sync (blocking disk I/O) — เรียกผ่าน asyncio.to_thread เท่านั้น
    ไม่ให้ blocking I/O นี้ไปแช่ event loop ตอนมีหลาย event ยิงพร้อมกัน"""
    with open(event.full_image_path, "rb") as f_full, \
         open(event.crop_image_path, "rb") as f_crop:
        return {
            "image_full": (f"{event.id}_full.jpg", f_full.read(), "image/jpeg"),
            "image_crop": (f"{event.id}_crop.jpg", f_crop.read(), "image/jpeg"),
        }


async def _send_webhook_request(client: httpx.AsyncClient, event: models.WebhookEvent):
    """ยิง 1 event จริงออกไป คืน (result_type, error_msg) เท่านั้น ไม่แตะ DB ในนี้เลย
    (กัน sync DB call ไปบล็อก event loop ตอนรันพร้อมกันหลาย coroutine ผ่าน asyncio.gather)"""

    # [SSRF Guard]: resolve+เช็ค IP ปลอดภัยซ้ำก่อนยิงจริงทุกครั้ง แล้วต่อ connection ไปยัง IP
    # ที่ resolve ได้ตรงๆ (ไม่ resolve ซ้ำอีกรอบตอน client.post())
    try:
        pinned_url, pin_kwargs = await asyncio.to_thread(build_pinned_request, event.target_url)
    except SSRFBlockedError as e:
        logging.warning(
            f"[SSRF Guard] ปฏิเสธการส่ง Event ID: {event.id} — {e} "
            f"(อาจเป็น DNS rebinding)"
        )
        return "ssrf_blocked", (
            f"ปฏิเสธการส่ง: {e} "
            "(อาจเกิดจาก DNS ถูกเปลี่ยนหลังผ่านการตรวจสอบตอนสร้าง endpoint ไปแล้ว)"
        )

    try:
        files = await asyncio.to_thread(_read_event_images, event)

        response = await client.post(
            pinned_url,
            data=event.payload,
            files=files,
            timeout=10,
            **pin_kwargs,
        )

        if response.status_code // 100 != 2:
            return "http_error", f"เซิร์ฟเวอร์ลูกค้าตอบกลับ HTTP {response.status_code}"

        if _is_valid_ack(event, response):
            return "success", None

        return "ack_mismatch", (
            f"ปลายทางตอบ 2xx แต่ body ไม่ตรง/parse ไม่ได้ "
            f"(คาดหวัง event_id={event.source_event_id}) ได้: {response.text[:300]!r}"
        )

    except FileNotFoundError as e:
        return "file_not_found", str(e)
    except Exception as e:
        return "error", str(e)


async def _send_with_semaphore(semaphore: asyncio.Semaphore, client: httpx.AsyncClient, event: models.WebhookEvent):
    """semaphore = Semaphore ของ endpoint ที่ event นี้สังกัด (ดู _build_endpoint_semaphores)"""
    async with semaphore:
        result_type, error_msg = await _send_webhook_request(client, event)
    return event, result_type, error_msg


def _build_endpoint_semaphores(events, per_endpoint_limit: int) -> dict:
    """สร้าง Semaphore แยกให้แต่ละ endpoint (key = webhook_endpoint_id) จาก event ที่กำลังจะส่งรอบนี้
    event ที่ไม่มี endpoint ผูก (webhook_endpoint_id=None) จะใช้ Semaphore ร่วมกันภายใต้ key None"""
    return {
        endpoint_id: asyncio.Semaphore(per_endpoint_limit)
        for endpoint_id in {e.webhook_endpoint_id for e in events}
    }


async def _get_suspended_user_ids(db: AsyncSession, user_ids: set) -> set:
    """คืน set ของ user_id ที่ is_suspended=True ในกลุ่ม user_ids ที่ให้มา (query ครั้งเดียว
    ต่อรอบ ไม่ query ทีละ event) ใช้ใน process_graveyard_resume เพื่อกัน event ของ user ที่ถูกระงับ
    ไม่ให้ถูกส่งออกไปจริง (ตัวส่งปกติกรองเรื่องนี้ใน SQL ตอนจองงานเลย ดู _claim_due_events)"""
    if not user_ids:
        return set()
    result = await db.execute(
        select(models.User.id).filter(
            models.User.id.in_(user_ids),
            models.User.is_suspended == True,  # noqa: E712
        )
    )
    return {uid for (uid,) in result.all()}


async def _record_endpoint_outcome(db: AsyncSession, endpoint_id: str, success: bool) -> int | None:
    """
    Circuit breaker bookkeeping แบบให้ DB นับเอง — ใช้ร่วมกันทั้งตัวส่งปกติและ graveyard
    ทำไมต้องให้ DB นับ: ตอนนี้ event ของ endpoint เดียวกันหลายตัวส่งพร้อมกันคนละ task ถ้าอ่านค่ามาบวกใน Python
    แล้วเขียนกลับ สองงานอาจอ่านได้ 1 พร้อมกันแล้วเขียน 2 ทั้งคู่ (นับหายไป 1) จึงสั่ง SET x = x + 1 ให้ DB บวกให้ทีละครั้ง
    - success -> ล้าง streak dead_letter (มี event ผ่านคั่นกลาง เริ่มนับใหม่)
    - ตกสุสาน -> streak + 1 ครบ DEAD_LETTER_THRESHOLD -> ตัดไฟ (is_healthy=False)
    คืนจำนวน dead letter ติดกัน ถ้าการเรียกครั้งนี้เป็นตัวที่ "เพิ่งตัดไฟ" พอดี (ไม่ใช่ คืน None)
    ไม่ commit ในนี้ caller เป็นคน commit
    """
    endpoint_t = models.WebhookEndpoint
    if success:
        await db.execute(
            update(endpoint_t)
            .where(endpoint_t.id == endpoint_id, endpoint_t.consecutive_dead_letters != 0)
            .values(consecutive_dead_letters=0)
            .execution_options(synchronize_session=False)
        )
        return None

    count = (await db.execute(
        update(endpoint_t)
        .where(endpoint_t.id == endpoint_id)
        .values(consecutive_dead_letters=endpoint_t.consecutive_dead_letters + 1)
        .returning(endpoint_t.consecutive_dead_letters)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    if count is None or count < DEAD_LETTER_THRESHOLD:
        return None

    # WHERE is_healthy = TRUE: ถ้าหลายงานครบ threshold พร้อมกัน จะมีแค่งานเดียวที่เปลี่ยนได้ -> แจ้งเตือนครั้งเดียว
    just_tripped = (await db.execute(
        update(endpoint_t)
        .where(endpoint_t.id == endpoint_id, endpoint_t.is_healthy.is_(True))
        .values(is_healthy=False)
        .returning(endpoint_t.id)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    return count if just_tripped is not None else None


async def _log_endpoint_tripped(db: AsyncSession, endpoint_id: str, consecutive_dead_letters: int) -> None:
    """เพิ่ม audit log ว่าระบบตัดไฟ endpoint นี้อัตโนมัติ (db.add เฉยๆ caller commit พร้อมการเปลี่ยนแปลงหลัก)"""
    url = (await db.execute(
        select(models.WebhookEndpoint.url).filter(models.WebhookEndpoint.id == endpoint_id)
    )).scalar_one_or_none()
    log_admin_action(
        db, None,
        action="webhook.auto_disable",
        target_type="webhook_endpoint",
        target_id=endpoint_id,
        detail={
            "url": url,
            "consecutive_dead_letters": consecutive_dead_letters,
        },
        actor_type="system",
    )


async def _notify_endpoint_tripped(db: AsyncSession, endpoint_id: str) -> None:
    """ส่งอีเมลแจ้งเจ้าของว่า endpoint ถูกตัดไฟ — เรียกหลัง commit แล้วเท่านั้น"""
    row = (await db.execute(
        select(models.User.email, models.WebhookEndpoint.url)
        .join(models.User, models.User.id == models.WebhookEndpoint.user_id)
        .filter(models.WebhookEndpoint.id == endpoint_id)
    )).first()
    if not row:
        return
    try:
        await asyncio.to_thread(send_webhook_endpoint_unhealthy_email, row.email, row.url)
    except RuntimeError as e:
        logging.error(f"ส่งอีเมลแจ้งเตือน endpoint ตัดไฟ (id={endpoint_id}) ไม่สำเร็จ: {e}")


def _next_event_state(event: models.WebhookEvent, result_type: str, error_msg: str | None):
    """คำนวณสถานะถัดไปของ event จากผลการส่ง (ไม่แตะ DB) คืน (status, attempt_count, next_retry_at)
    กติกาเหมือนเดิมทุกอย่าง: สำเร็จ -> success / ไม่พบไฟล์รูป -> dead_letter ทันที /
    ส่งไม่ผ่าน -> นัดลองใหม่อีก 3, 5, 10 นาที ครบแล้วยังไม่ผ่าน -> dead_letter"""
    if result_type == "success":
        return "success", event.attempt_count, None

    attempts = (event.attempt_count or 0) + 1
    if result_type == "file_not_found":
        logging.warning(f"ไม่พบไฟล์รูปของ Event ID: {event.id} | {error_msg}")
        return "dead_letter", attempts, None

    # ครอบคลุมทั้ง http_error, ack_mismatch, ssrf_blocked, error ธรรมดา — เข้า retry logic เดียวกันหมด
    logging.warning(f"ส่งข้อมูลไม่สำเร็จ Event ID: {event.id} | ประเภท: {result_type} | Error: {error_msg}")
    if attempts > 3:
        return "dead_letter", attempts, None
    delay_minutes = RETRY_DELAYS.get(attempts, 10)
    return "failed", attempts, datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)


# ---------------------------------------------------------------------------
# [Webhook Sender] ส่ง webhook ทันที และแยกส่งทีละ event ไม่ต้องรอกัน
#
# กริ่ง (เหมือนเดิม): POST /capture-event บันทึก event ลง DB เสร็จแล้วกดกริ่ง (ring_webhook_bell)
#   webhook_loop ตื่นมาทำงานทันที ระหว่างรอไม่แตะ DB และถ้าไม่มีใครกดกริ่งภายใน WEBHOOK_SWEEP_SECONDS
#   ก็ตื่นมาเช็คเองหนึ่งรอบ เพื่อเก็บงานที่ไม่มีใครกดกริ่งให้: งาน retry (3/5/10 นาที) และงานที่ค้างตอน restart
#
# เดิม: หยิบทั้งชุดแล้วรอให้ส่งครบทั้งชุดก่อนค่อยหยิบชุดใหม่ ปลายทางรายเดียวค้าง event ใหม่ของทุกรายต้องรอด้วย
# ใหม่: "จองงานใน DB แล้วแยกส่งทีละ event"
#   1. จอง (_claim_due_events): เลือก event ที่ถึงเวลาส่งด้วย SELECT ... FOR UPDATE SKIP LOCKED แล้วเลื่อน
#      next_retry_at ออกไป WEBHOOK_LEASE_SECONDS เป็น "ใบจอง" — รอบถัดไปจึงมองไม่เห็นงานนี้ (ไม่หยิบซ้ำ)
#      ถ้า backend ล่มกลางทาง พอใบจองหมดอายุ งานจะกลับมาให้ส่งใหม่เอง
#      SKIP LOCKED: ถ้ามี backend หลายตัวจองพร้อมกัน แถวที่ตัวอื่นล็อกอยู่จะถูกข้าม ไม่ได้แถวเดียวกัน
#   2. ส่ง (_deliver_one): แต่ละ event ได้ task ของตัวเอง ลูปไม่รอให้ส่งเสร็จ กลับไปรอกริ่งทันที
#   3. บันทึกผล (_save_delivery_result): เขียนผลเฉพาะเมื่อ next_retry_at ยังเป็นใบจองของเรา
#      ถ้าระหว่างส่งมี is_update เข้ามา (ตั้ง pending + เวลานัดเป็น "ตอนนี้") จะไม่เขียนทับ
#      event จึงยังเป็น pending พร้อมข้อมูลใหม่ และถูกส่งตามไปทันทีที่งานเดิมจบ
#
# เพดาน: ส่งพร้อมกันไม่เกิน REALTIME_PER_ENDPOINT_CONCURRENCY ต่อ endpoint และ WEBHOOK_MAX_IN_FLIGHT ทั้งระบบ
#   จองเท่าที่ส่งได้ทันทีเท่านั้น งานจะได้ไม่ต้องไปนั่งรอคิวจนใบจองหมดอายุ
# ข้อจำกัด: กริ่งดังได้แค่ใน process เดียว ถ้ามี backend หลายตัว ตัวอื่นจะเห็นงานใหม่ตอนรอบเช็คเอง (ไม่เกิน 10 วิ)
# ---------------------------------------------------------------------------
WEBHOOK_SWEEP_SECONDS = 10
WEBHOOK_SEND_TOTAL_TIMEOUT_SECONDS = 30   # เวลารวมสูงสุดต่อการส่ง 1 ครั้ง (timeout=10 ของ httpx นับแยกทีละช่วง)
WEBHOOK_LEASE_SECONDS = 60                # อายุใบจอง ต้องนานกว่า WEBHOOK_SEND_TOTAL_TIMEOUT_SECONDS เสมอ
WEBHOOK_MAX_IN_FLIGHT = 50                # ส่งพร้อมกันสูงสุดทั้งระบบ

_webhook_bell = asyncio.Event()
_in_flight: dict[str, asyncio.Task] = {}             # event.id -> task ที่กำลังส่ง
_in_flight_per_endpoint: dict[str | None, int] = {}  # webhook_endpoint_id -> จำนวนที่กำลังส่ง


def ring_webhook_bell() -> None:
    """กดกริ่ง: เรียกหลังบันทึก event ใหม่ (หรือรีเซ็ต event เดิมกลับเป็น pending) ลง DB สำเร็จแล้วเท่านั้น"""
    _webhook_bell.set()


async def webhook_loop() -> None:
    """ลูปส่ง webhook — main.py สร้างเป็น background task ตอนเปิดระบบ และ cancel ตอนปิดระบบ"""
    async with httpx.AsyncClient() as client:
        try:
            while True:
                try:
                    await asyncio.wait_for(_webhook_bell.wait(), timeout=WEBHOOK_SWEEP_SECONDS)
                except asyncio.TimeoutError:
                    pass  # ครบเวลาแล้วไม่มีใครกดกริ่ง -> เช็คเองหนึ่งรอบ (เก็บงาน retry)
                _webhook_bell.clear()
                try:
                    await _dispatch_due_events(client)
                except Exception as e:
                    # กันไว้ไม่ให้ลูปตายเงียบๆ (เช่น DB หลุดชั่วคราว) รอบหน้าค่อยลองใหม่
                    logging.error(f"webhook_loop ทำงานผิดพลาด: {e}")
        finally:
            await _cancel_in_flight()


async def _cancel_in_flight() -> None:
    """ตอนปิดระบบ: ยกเลิกงานที่กำลังส่งค้างอยู่ ใบจองใน DB ยังอยู่ พอหมดอายุ (WEBHOOK_LEASE_SECONDS)
    backend ที่เปิดขึ้นมาใหม่จะหยิบไปส่งต่อเอง (ถ้ารอบที่ถูกยกเลิกส่งถึงลูกค้าไปแล้ว ลูกค้าจะได้ซ้ำ กันด้วย event_id)"""
    tasks = list(_in_flight.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _dispatch_due_events(client: httpx.AsyncClient) -> None:
    """จองงานที่ถึงเวลาส่ง แล้วปล่อยแต่ละ event ไปส่งใน task ของตัวเอง
    วนจองจนกว่าจะไม่มีงานเหลือ หรือเต็มเพดาน (เต็มแล้ว งานที่ส่งเสร็จจะกดกริ่งเรียกรอบใหม่เอง)"""
    while True:
        free_slots = WEBHOOK_MAX_IN_FLIGHT - len(_in_flight)
        if free_slots <= 0:
            return
        claimed, lease_until, changed = await _claim_due_events(free_slots)
        for event in claimed:
            _start_delivery(client, event, lease_until)
        if not changed:
            return


async def _claim_due_events(limit: int):
    """
    จองงาน 1 ชุด คืน (event ที่จองได้, เวลาหมดอายุใบจอง, รอบนี้เปลี่ยนอะไรใน DB ไหม)
    งานที่ไม่แตะเลย (เหมือนเดิม): endpoint ถูกปิดใช้งาน (is_active=False), เจ้าของถูกระงับ
    endpoint ถูกตัดไฟอยู่ (is_healthy=False): ไม่ส่งจริง ย้ายเข้า dead_letter ทันที (เหมือนเดิม)
    """
    event_t, endpoint_t, user_t = models.WebhookEvent, models.WebhookEndpoint, models.User
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=WEBHOOK_LEASE_SECONDS)

    query = (
        select(event_t, endpoint_t.is_healthy)
        .outerjoin(endpoint_t, endpoint_t.id == event_t.webhook_endpoint_id)
        .outerjoin(user_t, user_t.id == event_t.user_id)
        .filter(
            event_t.status.in_(["pending", "failed"]),
            or_(event_t.next_retry_at <= now, event_t.next_retry_at.is_(None)),
            event_t.deleted_at.is_(None),
            or_(endpoint_t.id.is_(None), endpoint_t.is_active.is_(True)),
            or_(user_t.id.is_(None), user_t.is_suspended.is_(False)),
        )
        .order_by(event_t.created_at)
        .limit(limit)
        .with_for_update(of=event_t, skip_locked=True)
    )
    # งานที่ process นี้กำลังส่งอยู่ ไม่จองซ้ำ — เช่น is_update เพิ่งรีเซ็ตเวลานัดของ event ที่กำลังส่ง:
    # รอให้งานเดิมจบก่อนค่อยส่งข้อมูลใหม่ตามไป ลูกค้าจะได้รับเรียงตามลำดับ
    if _in_flight:
        query = query.filter(event_t.id.notin_(list(_in_flight)))
    # endpoint ที่ส่งพร้อมกันเต็มเพดานแล้ว ไม่จองเพิ่ม
    busy = {ep for ep, n in _in_flight_per_endpoint.items() if n >= REALTIME_PER_ENDPOINT_CONCURRENCY}
    if None in busy:
        query = query.filter(event_t.webhook_endpoint_id.is_not(None))
    busy_ids = [ep for ep in busy if ep is not None]
    if busy_ids:
        query = query.filter(or_(event_t.webhook_endpoint_id.is_(None), event_t.webhook_endpoint_id.notin_(busy_ids)))

    claimed = []
    changed = False
    taken = dict(_in_flight_per_endpoint)
    async with SessionLocal() as db:
        rows = (await db.execute(query)).all()
        for event, endpoint_healthy in rows:
            endpoint_id = event.webhook_endpoint_id
            if endpoint_id is not None and endpoint_healthy is False:
                # Circuit breaker: endpoint ถูกตัดไฟอยู่ -> ข้าม 3 รอบ retry เข้าสุสานทันที ประหยัดเวลา
                event.status = "dead_letter"
                event.next_retry_at = None
                changed = True
                logging.info(
                    f"Event ID: {event.id} ข้ามการส่งจริง เพราะ endpoint id={endpoint_id} "
                    f"ถูกตัดไฟอยู่ (is_healthy=False)"
                )
                continue
            if taken.get(endpoint_id, 0) >= REALTIME_PER_ENDPOINT_CONCURRENCY:
                continue  # endpoint นี้เต็มเพดานในชุดนี้แล้ว ไม่จอง ปล่อยไว้ให้ชุดหน้า
            taken[endpoint_id] = taken.get(endpoint_id, 0) + 1
            event.next_retry_at = lease_until  # ใบจอง
            claimed.append(event)
            changed = True
        await db.commit()
    return claimed, lease_until, changed


def _start_delivery(client: httpx.AsyncClient, event: models.WebhookEvent, lease_until: datetime) -> None:
    endpoint_id = event.webhook_endpoint_id
    _in_flight_per_endpoint[endpoint_id] = _in_flight_per_endpoint.get(endpoint_id, 0) + 1
    _in_flight[event.id] = asyncio.create_task(_deliver_one(client, event, lease_until))


async def _deliver_one(client: httpx.AsyncClient, event: models.WebhookEvent, lease_until: datetime) -> None:
    """ส่ง event 1 ตัวแล้วบันทึกผล — รันเป็น task ของตัวเอง ไม่มีใครต้องรอใคร"""
    try:
        try:
            result_type, error_msg = await asyncio.wait_for(
                _send_webhook_request(client, event), timeout=WEBHOOK_SEND_TOTAL_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            result_type, error_msg = "error", f"ส่งไม่เสร็จภายใน {WEBHOOK_SEND_TOTAL_TIMEOUT_SECONDS} วินาที"
        await _save_delivery_result(event, lease_until, result_type, error_msg)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # บันทึกผลไม่ได้ (เช่น DB หลุด) ใบจองยังอยู่ หมดอายุแล้ว event จะถูกส่งใหม่เอง
        logging.error(f"ส่ง/บันทึกผล Event ID: {event.id} ผิดพลาด: {e}")
    finally:
        _in_flight.pop(event.id, None)
        endpoint_id = event.webhook_endpoint_id
        remaining = _in_flight_per_endpoint.get(endpoint_id, 1) - 1
        if remaining > 0:
            _in_flight_per_endpoint[endpoint_id] = remaining
        else:
            _in_flight_per_endpoint.pop(endpoint_id, None)
        # มีที่ว่างเพิ่ม หรือ event นี้อาจถูก is_update ระหว่างส่ง -> ให้ลูปดูว่ามีงานรออยู่ไหม
        ring_webhook_bell()


async def _save_delivery_result(event: models.WebhookEvent, lease_until: datetime,
                                result_type: str, error_msg: str | None) -> None:
    status, attempts, next_retry_at = _next_event_state(event, result_type, error_msg)
    endpoint_id = event.webhook_endpoint_id
    event_t = models.WebhookEvent

    async with SessionLocal() as db:
        saved = (await db.execute(
            update(event_t)
            .where(
                event_t.id == event.id,
                event_t.next_retry_at == lease_until,  # ใบจองยังเป็นของเรา = ไม่มีใครแก้ event นี้ระหว่างส่ง
                event_t.status.in_(["pending", "failed"]),
            )
            .values(status=status, attempt_count=attempts, next_retry_at=next_retry_at)
            .execution_options(synchronize_session=False)
        )).rowcount == 1
        if not saved:
            logging.info(
                f"Event ID: {event.id} ถูกเปลี่ยนระหว่างส่ง (เช่นกล้องส่งค่าที่แม่นขึ้นมา) "
                f"ไม่เขียนผลรอบนี้ทับ — ข้อมูลล่าสุดจะถูกส่งตามไป"
            )

        tripped_count = None
        if endpoint_id is not None:
            if status == "success":
                await _record_endpoint_outcome(db, endpoint_id, success=True)
            elif status == "dead_letter" and saved:
                tripped_count = await _record_endpoint_outcome(db, endpoint_id, success=False)
        if tripped_count is not None:
            await _log_endpoint_tripped(db, endpoint_id, tripped_count)
        await db.commit()

        if tripped_count is not None:
            try:
                await _notify_endpoint_tripped(db, endpoint_id)
            except Exception as e:
                # ผลการส่งบันทึกไปแล้ว อีเมลพังไม่ควรทำให้ดูเหมือนบันทึกผลไม่สำเร็จ
                logging.error(f"แจ้งเตือนเจ้าของ endpoint ที่ถูกตัดไฟ (id={endpoint_id}) ไม่สำเร็จ: {e}")


async def _ping_endpoint(client: httpx.AsyncClient, url: str) -> bool:
    # [SSRF Guard]: resolve+เช็ค IP ปลอดภัยซ้ำก่อนยิงจริงเหมือน _send_webhook_request แล้วต่อ
    # connection ไปยัง IP ที่ resolve ได้ตรงๆ (ไม่ resolve ซ้ำตอน client.post())
    try:
        pinned_url, pin_kwargs = await asyncio.to_thread(build_pinned_request, url)
    except SSRFBlockedError as e:
        logging.warning(f"[SSRF Guard] ข้าม health check เพราะ {url} — {e}")
        return False

    try:
        test_event_id, dummy_payload, dummy_files = await asyncio.to_thread(build_test_webhook_payload)

        response = await client.post(
            pinned_url, data=dummy_payload, files=dummy_files, timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
            **pin_kwargs,
        )
        if response.status_code // 100 != 2:
            return False

        body = response.json()
        return isinstance(body, dict) and body.get("event_id") == test_event_id
    except RuntimeError as e:
        logging.error(f"[Health Check] ไม่สามารถโหลดรูปทดสอบ webhook: {e}")
        return False
    except Exception:
        return False


# กันไม่ให้ graveyard resume รันซ้อนกันเอง (scheduler ทุก 30 นาที + resume_endpoint_now ที่ admin
# สั่งผ่าน BackgroundTasks รันใน event loop เดียวกัน) — ถ้าซ้อนกัน dead_letter ชุดเดียวกันอาจถูกส่งซ้ำ 2 รอบ
_graveyard_lock = asyncio.Lock()


def _event_files_exist(event: models.WebhookEvent) -> bool:
    return bool(event.full_image_path and event.crop_image_path
                and os.path.exists(event.full_image_path) and os.path.exists(event.crop_image_path))


async def process_graveyard_resume(endpoint_ids: list[int] | None = None):
    """
    Job B — แยกเด็ดขาดจาก Job A ทั้งคิวและ Semaphore (ต่อ endpoint: RESUME_PER_ENDPOINT_CONCURRENCY)
    ทุก 30 นาที (default): ส่ง event ที่ค้างอยู่ใน dead_letter ซ้ำ ให้ endpoint ที่ปลายทางกลับมาแล้ว

    endpoint ที่ถูกเช็ค (ต้อง is_active=True เสมอ):
    - endpoint ที่ถูกตัดไฟ (is_healthy=False) — เช็คว่าฟื้นหรือยัง ถ้าฟื้นเปิดไฟกลับ
    - [แก้ไข] endpoint ที่ยังไม่ถูกตัดไฟ แต่มี event ค้างใน dead_letter — เคสปลายทางล่มสั้นๆ
      มี event ตกสุสานไม่ถึง DEAD_LETTER_THRESHOLD เดิม event พวกนี้ไม่เคยถูกส่งซ้ำเลย
      (เพราะเดิม job นี้ดูเฉพาะ is_healthy=False) ค้างอยู่ใน dead_letter ถาวร

    ทุก endpoint ต้องผ่าน health check (_ping_endpoint) ก่อนถึงจะ resend จริง
    event ที่ไฟล์รูปหายไปแล้ว (ส่งยังไงก็ไม่ผ่าน) จะไม่ถูกส่งซ้ำ และไม่นับเป็น streak ตัดไฟ

    endpoint_ids: ไม่ระบุ (None) = ทุก endpoint (เรียกจาก scheduler ทุก 30 นาที)
    ระบุมา = จำกัด scope เฉพาะ endpoint ที่ระบุ ใช้ตอน services/admin_service.py:set_webhook_status
    เรียกทันทีหลัง admin เปิด endpoint ที่เคยถูกตัดไฟกลับมา
    """
    async with _graveyard_lock:
        await _process_graveyard_resume(endpoint_ids)


async def _process_graveyard_resume(endpoint_ids: list[int] | None = None):
    db: AsyncSession = SessionLocal()
    try:
        has_dead_letters = (
            select(models.WebhookEvent.id)
            .filter(
                models.WebhookEvent.webhook_endpoint_id == models.WebhookEndpoint.id,
                models.WebhookEvent.status == "dead_letter",
                models.WebhookEvent.deleted_at.is_(None),
            )
            .exists()
        )
        query = select(models.WebhookEndpoint).filter(
            models.WebhookEndpoint.is_active == True,  # noqa: E712
            or_(models.WebhookEndpoint.is_healthy == False, has_dead_letters),  # noqa: E712
        )
        if endpoint_ids is not None:
            query = query.filter(models.WebhookEndpoint.id.in_(endpoint_ids))
        candidate_endpoints = (await db.execute(query)).scalars().all()

        if not candidate_endpoints:
            return

        reachable_endpoints = []   # ping ผ่าน -> resend dead_letter ของ endpoint นี้ได้
        recovered_endpoints = []   # เคยถูกตัดไฟ แล้วเพิ่งฟื้นรอบนี้
        results = []
        dead_events = []
        async with httpx.AsyncClient() as client:
            for endpoint in candidate_endpoints:
                if not await _ping_endpoint(client, endpoint.url):
                    continue
                reachable_endpoints.append(endpoint)
                if not endpoint.is_healthy:
                    endpoint.is_healthy = True
                    endpoint.consecutive_dead_letters = 0
                    recovered_endpoints.append(endpoint)

            for endpoint in recovered_endpoints:
                log_admin_action(
                    db, None,
                    action="webhook.auto_recover",
                    target_type="webhook_endpoint",
                    target_id=endpoint.id,
                    detail={"url": endpoint.url},
                    actor_type="system",
                )

            # เปิดไฟให้ endpoint ที่ฟื้นก่อน commit ทันที แม้ resume ด้านล่างจะพังก็ไม่เสีย progress ตรงนี้
            await db.commit()

            if not reachable_endpoints:
                return

            reachable_ids = [ep.id for ep in reachable_endpoints]
            dead_events_result = await db.execute(
                select(models.WebhookEvent)
                .filter(
                    models.WebhookEvent.webhook_endpoint_id.in_(reachable_ids),
                    models.WebhookEvent.status == "dead_letter",
                    models.WebhookEvent.deleted_at.is_(None),
                )
                .order_by(models.WebhookEvent.created_at.asc())
            )
            dead_events = dead_events_result.scalars().all()

            if not dead_events:
                logging.info(
                    f"[Graveyard Resume] endpoint ฟื้น {len(recovered_endpoints)} ตัว "
                    f"แต่ไม่มี event ค้างในสุสาน"
                )
                return

            # [Suspend Guard]: endpoint ฟื้นแล้วก็จริง แต่ถ้าเจ้าของ event ยังถูกระงับอยู่ ไม่ควร
            # resume ส่งให้ — ตัดออกจากรอบนี้ไปก่อน (ยังอยู่ใน dead_letter รอรอบหน้าหลังปลดระงับ)
            suspended_user_ids = await _get_suspended_user_ids(db, {e.user_id for e in dead_events if e.user_id})
            skipped_suspended = [e for e in dead_events if e.user_id in suspended_user_ids]
            dead_events = [e for e in dead_events if e.user_id not in suspended_user_ids]

            if skipped_suspended:
                logging.info(
                    f"[Graveyard Resume] ข้าม {len(skipped_suspended)} event เพราะเจ้าของถูกระงับอยู่"
                )

            # ไฟล์รูปหายไปแล้ว (เช่นถูก cleanup) ส่งซ้ำยังไงก็ไม่ผ่าน -> ไม่ส่ง และไม่ให้นับเป็น streak
            # ตัดไฟ endpoint ที่ปลายทางปกติดีอยู่
            files_ok = await asyncio.gather(*(asyncio.to_thread(_event_files_exist, e) for e in dead_events))
            missing_files = [e for e, ok in zip(dead_events, files_ok) if not ok]
            dead_events = [e for e, ok in zip(dead_events, files_ok) if ok]
            if missing_files:
                logging.warning(
                    f"[Graveyard Resume] ข้าม {len(missing_files)} event เพราะไม่พบไฟล์รูปแล้ว (ส่งซ้ำไม่ได้)"
                )

            if not dead_events:
                return

            endpoint_semaphores = _build_endpoint_semaphores(dead_events, RESUME_PER_ENDPOINT_CONCURRENCY)
            results = await asyncio.gather(
                *(
                    _send_with_semaphore(endpoint_semaphores[event.webhook_endpoint_id], client, event)
                    for event in dead_events
                )
            )

        endpoints_by_id = {ep.id: ep for ep in reachable_endpoints}
        tripped_counts = {}  # endpoint.id -> จำนวน dead letter ติดกันตอนเพิ่งตัดไฟ
        for event, result_type, error_msg in results:
            endpoint = endpoints_by_id.get(event.webhook_endpoint_id)
            if result_type == "success":
                event.status = "success"
                if endpoint:
                    await _record_endpoint_outcome(db, endpoint.id, success=True)
            else:
                logging.warning(f"Resume ส่งไม่สำเร็จอีกครั้ง Event ID: {event.id} | {result_type} | {error_msg}")
                event.status = "dead_letter"  # กลับไปสุสาน รอรอบหน้า (อีก 30 นาที)
                if endpoint:
                    count = await _record_endpoint_outcome(db, endpoint.id, success=False)
                    if count is not None:
                        tripped_counts[endpoint.id] = count

        for endpoint_id, count in tripped_counts.items():
            await _log_endpoint_tripped(db, endpoint_id, count)

        await db.commit()

        for endpoint_id in tripped_counts:
            await _notify_endpoint_tripped(db, endpoint_id)

        logging.info(
            f"[Graveyard Resume] endpoint ที่ฟื้น {len(recovered_endpoints)} ตัว, "
            f"resume event ทั้งหมด {len(dead_events)} รายการ"
        )

    except Exception as e:
        await db.rollback()
        logging.error(f"Graveyard resume job ทำงานผิดพลาด: {e}")
    finally:
        await db.close()


async def _verify_one_camera_rtsp(camera: models.Camera) -> tuple[models.Camera, bool]:
    """เช็ค RTSP กล้องตัวเดียว (ไม่แตะ DB เลย) — คืน (camera, is_ok) ให้ caller เอาไปอัปเดต DB
    เองแบบ sequential ทีหลัง (AsyncSession ตัวเดียวกันเขียนพร้อมกันหลาย coroutine ไม่ได้ —
    เหตุผลเดียวกับ comment เรื่อง half-async trap ใน _notify_endpoint_tripped ด้านบน)
    ตัว timeout ยังเป็น per-camera เหมือนเดิม (CAMERA_VERIFY_TIMEOUT_SECONDS) — แค่หลายตัว
    รันพร้อมกันได้แล้วผ่าน semaphore ของ caller (_verify_with_semaphore) แทนที่จะรอกันทีละตัว"""
    is_ok = await check_camera_rtsp(camera.rtsp_url, timeout_seconds=CAMERA_VERIFY_TIMEOUT_SECONDS)
    return camera, is_ok


async def _verify_with_semaphore(semaphore: asyncio.Semaphore, camera: models.Camera):
    async with semaphore:
        return await _verify_one_camera_rtsp(camera)


async def verify_pending_cameras():
    """
    Background verification จังหวะที่ 2 (จังหวะที่ 1 คือ SSRF guard ตอน POST /partner/cameras)
    เช็คกล้องที่ verification_status อยู่ใน (pending, failed) และยังไม่ครบโควต้าการลอง
    เฉพาะกล้องที่ "ยังไม่เคยผ่านการตรวจเลย" (verified_at IS NULL) เท่านั้น — กล้องที่เคยใช้งานได้แล้ว
    แต่ admin กดตรวจแล้วไม่ผ่านชั่วคราว (failed) จะไม่ถูกดึงมาตรวจซ้ำ/ลบทิ้งอัตโนมัติที่นี่

    ผ่าน -> is_active=True, verification_status='verified'
    ไม่ผ่านแต่ยังไม่ครบโควต้า -> verification_status='failed' รอ job รอบหน้าลองใหม่ (ทุก 20 วิ)
    ไม่ผ่านและครบโควต้าแล้ว -> [Bounded Retry] ลบ row ทิ้งเลย ไม่ค้างเป็น failed ตลอดไป

    [Concurrency]: เช็ค RTSP ของแต่ละกล้องแบบขนานกัน (สูงสุด CAMERA_VERIFY_CONCURRENCY ตัว
    พร้อมกัน ผ่าน asyncio.gather + Semaphore) แทนที่จะวน sequential ทีละตัวเหมือนเดิม — เดิม
    กล้องที่มาทีหลังในคิวต้องรอกล้องก่อนหน้าเช็คเสร็จก่อน (worst case N ตัว ×
    CAMERA_VERIFY_TIMEOUT_SECONDS วิ ต่อรอบ) พอ interval ของ job นี้ลดเหลือ 30 วิ แค่ 2-3 กล้อง
    pending พร้อมกันก็ทำให้รอบเดียวเกิน interval แล้ว การเช็คขนานทำให้เวลารวมของรอบเหลือแค่
    ceil(N / CAMERA_VERIFY_CONCURRENCY) × timeout เท่านั้น (ตัวใครตัวมัน ไม่ต้องรอกัน) —
    ส่วนขั้นอัปเดต DB (verify_attempt_count, verification_status, ลบกล้องที่เกินโควต้า ฯลฯ)
    ยังต้องทำ sequential เหมือนเดิมหลัง gather เสร็จ เพราะ session เดียวกันเขียนพร้อมกันไม่ได้
    (แต่ส่วนนี้เร็ว ไม่มี I/O รอกล้องจริง จึงไม่ใช่คอขวด)
    """
    db: AsyncSession = SessionLocal()
    try:
        result = await db.execute(
            select(models.Camera).filter(
                models.Camera.verification_status.in_(["pending", "failed"]),
                models.Camera.verify_attempt_count < CAMERA_VERIFY_MAX_ATTEMPTS,
                models.Camera.verified_at.is_(None),
            )
        )
        cameras_to_check = result.scalars().all()

        if not cameras_to_check:
            return

        semaphore = asyncio.Semaphore(CAMERA_VERIFY_CONCURRENCY)
        results = await asyncio.gather(
            *(_verify_with_semaphore(semaphore, camera) for camera in cameras_to_check)
        )

        for camera, is_ok in results:
            camera.verify_attempt_count += 1

            if is_ok:
                camera.is_active = True
                camera.verification_status = "verified"
                camera.verified_at = datetime.now(timezone.utc)
                logging.info(f"[Camera Verify] camera id={camera.id} เชื่อมต่อ RTSP สำเร็จ -> verified")

            elif camera.verify_attempt_count >= CAMERA_VERIFY_MAX_ATTEMPTS:
                cam_id = camera.id
                logging.warning(
                    f"[Camera Verify] camera id={cam_id} เชื่อมต่อ RTSP ไม่สำเร็จครบ "
                    f"{CAMERA_VERIFY_MAX_ATTEMPTS} ครั้ง -> ลบออกจากระบบ (partner ต้องสร้างใหม่เอง)"
                )

                log_admin_action(
                    db, None,
                    action="camera.auto_delete",
                    target_type="camera",
                    target_id=cam_id,
                    detail={
                        "owner_user_id": camera.owner_user_id,
                        "verify_attempt_count": camera.verify_attempt_count,
                        "storage_cleaned": True,
                    },
                    actor_type="system",
                )

                await db.execute(
                    update(models.WebhookEvent)
                    .where(models.WebhookEvent.camera_id == cam_id)
                    .values(camera_id=None)
                )
                await db.delete(camera)
                delete_camera_storage(cam_id)

            else:
                camera.verification_status = "failed"
                logging.warning(
                    f"[Camera Verify] camera id={camera.id} เชื่อมต่อ RTSP ไม่สำเร็จ "
                    f"(ครั้งที่ {camera.verify_attempt_count}/{CAMERA_VERIFY_MAX_ATTEMPTS}) -> ลองใหม่รอบหน้า"
                )

        await db.commit()

    except Exception as e:
        await db.rollback()
        logging.error(f"Camera verification job ทำงานผิดพลาด: {e}")
    finally:
        await db.close()

async def cleanup_unverified_users():
    """
    ลบ user ที่สมัครแล้วไม่ยืนยัน OTP ภายในเวลาที่กำหนด (hard delete)
    เกณฑ์: created_at เกิน UNVERIFIED_USER_EXPIRE_HOURS ชั่วโมง และ is_verified == False
    ลบ OtpVerification ที่ผูกกับ user นั้นก่อน (กัน FK constraint) แล้วค่อยลบ user
    """
    db: AsyncSession = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=UNVERIFIED_USER_EXPIRE_HOURS)

        result = await db.execute(
            select(models.User).filter(
                models.User.is_verified == False,  # noqa: E712
                models.User.created_at <= cutoff,
            )
        )
        stale_users = result.scalars().all()

        if not stale_users:
            return

        for user in stale_users:
            await db.execute(
                delete(models.OtpVerification).where(models.OtpVerification.user_id == user.id)
            )
            logging.info(
                f"ลบ user ที่ไม่ verify เกิน {UNVERIFIED_USER_EXPIRE_HOURS} ชม.: "
                f"{user.email} (id={user.id}, สมัครเมื่อ {user.created_at})"
            )
            await db.delete(user)

        await db.commit()

    except Exception as e:
        await db.rollback()
        logging.error(f"Cleanup unverified users ทำงานผิดพลาด: {e}")
    finally:
        await db.close()


async def cleanup_old_plate_data():
    db: AsyncSession = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=PLATE_DATA_RETENTION_DAYS)
        now = datetime.now(timezone.utc)

        result = await db.execute(
            update(models.WebhookEvent)
            .where(
                models.WebhookEvent.created_at <= cutoff,
                models.WebhookEvent.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )

        await db.commit()

        if result.rowcount:
            logging.info(
                f"Soft delete ข้อมูลป้ายทะเบียนที่เก็บเกิน {PLATE_DATA_RETENTION_DAYS} วัน "
                f"จำนวน {result.rowcount} รายการ"
            )

    except Exception as e:
        await db.rollback()
        logging.error(f"Cleanup ข้อมูลป้ายทะเบียนเก่าทำงานผิดพลาด: {e}")
    finally:
        await db.close()

async def cleanup_expired_revoked_tokens():
    """ลบ record ใน revoked_tokens ที่ revoked_expires_at ผ่านไปแล้ว"""
    db: AsyncSession = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            delete(models.RevokedToken).where(models.RevokedToken.revoked_expires_at <= now)
        )
        await db.commit()
        if result.rowcount:
            logging.info(f"ลบ revoked token ที่หมดอายุไปแล้ว {result.rowcount} รายการ")
    except Exception as e:
        await db.rollback()
        logging.error(f"Cleanup revoked tokens ทำงานผิดพลาด: {e}")
    finally:
        await db.close()


async def cleanup_expired_refresh_tokens():
    """ลบ refresh token ที่หมดอายุไปแล้ว (ไม่ว่าจะเคยถูก rotate/revoke ไปก่อนหน้าหรือไม่ก็ตาม)"""
    db: AsyncSession = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            delete(models.RefreshToken).where(models.RefreshToken.expires_at <= now)
        )
        await db.commit()
        if result.rowcount:
            logging.info(f"ลบ refresh token ที่หมดอายุไปแล้ว {result.rowcount} รายการ")
    except Exception as e:
        await db.rollback()
        logging.error(f"Cleanup refresh tokens ทำงานผิดพลาด: {e}")
    finally:
        await db.close()

async def cleanup_old_otp_records():
    """ลบ OtpVerification ที่ค้างเกิน OTP_RETENTION_DAYS นับจากวันหมดอายุ"""
    db: AsyncSession = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=OTP_RETENTION_DAYS)
        result = await db.execute(
            delete(models.OtpVerification).where(models.OtpVerification.expires_at <= cutoff)
        )
        await db.commit()
        if result.rowcount:
            logging.info(f"ลบ OTP record ที่หมดอายุไปแล้ว {result.rowcount} รายการ")
    except Exception as e:
        await db.rollback()
        logging.error(f"Cleanup OTP records ทำงานผิดพลาด: {e}")
    finally:
        await db.close()

async def cleanup_old_audit_logs():
    """ลบ AdminAuditLog ที่เก่าเกิน ADMIN_AUDIT_LOG_RETENTION_DAYS (ดีฟอลต์ 365 วัน)"""
    db: AsyncSession = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=ADMIN_AUDIT_LOG_RETENTION_DAYS)
        result = await db.execute(
            delete(models.AdminAuditLog).where(models.AdminAuditLog.created_at <= cutoff)
        )
        await db.commit()
        if result.rowcount:
            logging.info(
                f"ลบ Admin Audit Log ที่เก็บเกิน {ADMIN_AUDIT_LOG_RETENTION_DAYS} วัน "
                f"จำนวน {result.rowcount} รายการ"
            )
    except Exception as e:
        await db.rollback()
        logging.error(f"Cleanup admin audit log ทำงานผิดพลาด: {e}")
    finally:
        await db.close()

async def cleanup_orphaned_cameras_job():
    """สแกนและลบรูปภาพ/Log ของกล้องที่ไม่มีในระบบแล้ว (Orphaned storage) ทุก 24 ชั่วโมง"""
    db: AsyncSession = SessionLocal()
    try:
        await prune_orphaned_camera_storage(db)
    finally:
        await db.close()

async def resume_endpoint_now(endpoint_id: int) -> None:
    await process_graveyard_resume(endpoint_ids=[endpoint_id])

def start_scheduler():
    scheduler = AsyncIOScheduler()
    # ตัวส่ง webhook ปกติไม่อยู่ใน scheduler — อยู่ใน webhook_loop (กริ่ง + จองงาน) ดูด้านบน
    scheduler.add_job(process_graveyard_resume, 'interval', minutes=30)
    scheduler.add_job(verify_pending_cameras, 'interval', seconds=20)
    scheduler.add_job(cleanup_orphaned_cameras_job, 'interval', hours=24)
    scheduler.add_job(cleanup_unverified_users, 'interval', hours=1)
    scheduler.add_job(cleanup_old_plate_data, 'interval', hours=24)
    scheduler.add_job(cleanup_expired_revoked_tokens, 'interval', hours=1)
    scheduler.add_job(cleanup_expired_refresh_tokens, 'interval', hours=1)
    scheduler.add_job(cleanup_old_otp_records, 'interval', hours=24)
    scheduler.add_job(cleanup_old_audit_logs, 'interval', hours=24)
    scheduler.start()
    return scheduler