from fastapi import APIRouter, Depends, Query, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import require_admin
from smartlpr.pagination import PageParams
from services import admin_service

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/dashboard", response_model=schemas.AdminDashboardResponse)
async def get_admin_dashboard(
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """
    ภาพรวมระบบสำหรับหน้าแรกของโซน "ผู้ดูแลระบบ" — รวมตัวเลขสำคัญจากทุกโมดูลไว้ endpoint เดียว
    กัน frontend ต้องยิงหลาย request แยกกัน (access-requests, cameras, webhooks ฯลฯ) ตอนโหลด
    หน้าเดียว

    [ทำไม sequential await ไม่ใช้ asyncio.gather]: AsyncSession ตัวเดียวกัน (db จาก
    Depends(get_db)) ยิงหลาย query พร้อมกันแบบ concurrent ไม่ได้ (ไม่ coroutine-safe — เหตุผล
    เดียวกับที่ worker.py:_notify_endpoints_tripped เคยแก้ half-async trap ไว้) endpoint นี้
    เรียกไม่บ่อย (admin เข้ามาดูเป็นครั้งคราว ไม่ใช่ realtime polling) จึงไม่คุ้มไปพยายาม
    optimize รวมเป็น query เดียวที่อ่านยากขึ้นแลกความเร็วที่แทบไม่ต่างกันในทางปฏิบัติ
    """
    return await admin_service.get_admin_dashboard(db=db, admin=admin)


@router.get("/queue-events", response_model=schemas.AdminQueueEventPage)
async def list_queue_events(
    status_filter: Optional[str] = Query(None, alias="status", pattern="^(all|pending|failed|dead_letter)$", description="pending / failed / dead_letter / all"),
    webhook_url: Optional[str] = Query(None, max_length=2048, description="กรองเฉพาะ URL ปลายทางนี้ (ตรงตัว)"),
    q: Optional[str] = Query(None, max_length=100, description="ค้นหาใน URL / อีเมล / username / ทะเบียน / จังหวัด / กล้อง"),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """
    ดึงรายการ Webhook Event ที่อยู่ในคิว (pending / failed) หรือ dead_letter พร้อมข้อมูลเจ้าของ (User)
    แบบแบ่งหน้า (page / page_size) + กรองตาม Webhook และค้นหาได้ — webhooks ในผลลัพธ์ใช้ทำ dropdown
    """
    return await admin_service.list_queue_events(
        status_filter=status_filter,
        webhook_url=webhook_url,
        search=q,
        page_params=page_params,
        db=db,
        admin=admin,
    )


@router.get("/access-requests", response_model=schemas.PaginatedResponse[schemas.AccessRequestResponse])
async def list_access_requests(
    status_filter: Optional[str] = Query(
        default="pending",
        alias="status",
        description="กรองตามสถานะ: pending / approved / rejected (ไม่ใส่ = ดูทั้งหมด)",
    ),
    order: Optional[str] = Query(default="desc", pattern="^(asc|desc)$"),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.list_access_requests(
        status_filter=status_filter,
        order=order,
        page_params=page_params,
        db=db,
        admin=admin,
    )


@router.get("/access-requests/{request_id}", response_model=schemas.AccessRequestResponse)
async def get_access_request_detail(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.get_access_request_detail(
        request_id=request_id,
        db=db,
        admin=admin,
    )


@router.patch("/access-requests/{request_id}", response_model=schemas.AccessRequestResponse)
async def review_access_request(
    request_id: int,
    payload: schemas.ReviewDecision,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.review_access_request(
        request_id=request_id,
        payload=payload,
        ip_address=request.client.host,
        db=db,
        admin=admin,
    )


@router.get("/cameras", response_model=schemas.PaginatedResponse[schemas.CameraAdminResponse])
async def list_cameras(
    camera_id: Optional[str] = Query(
        default=None,
        description="กรองเฉพาะกล้องที่ ID มีข้อความนี้อยู่ (partial, case-insensitive) — ไม่ระบุ = ดูทั้งหมด",
    ),
    owner_user_id: Optional[str] = Query(
        default=None,
        description="กรองเฉพาะกล้องของเจ้าของ (owner_user_id) คนนี้เท่านั้น (exact match) — ไม่ระบุ = ดูทั้งหมด",
    ),
    owner_email: Optional[str] = Query(
        default=None,
        description="กรองเฉพาะกล้องของเจ้าของที่อีเมลมีข้อความนี้อยู่ (partial, case-insensitive) — ไม่ระบุ = ดูทั้งหมด",
    ),
    search: Optional[str] = Query(
        default=None,
        description="ค้นหาจาก Camera ID หรือ Owner Email (partial, case-insensitive) — ใช้ช่องเดียว",
    ),
    order: Optional[str] = Query(
        default="desc",
        pattern="^(asc|desc)$",
        description="เรียงลำดับ: desc (ล่าสุด), asc (หลังสุด/เก่าสุด)",
    ),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.list_cameras(
        camera_id=camera_id,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        search=search,
        order=order,
        page_params=page_params,
        db=db,
        admin=admin,
    )


@router.post("/cameras/{camera_id}/verify", response_model=schemas.CameraVerificationResult)
async def verify_admin_camera(
    camera_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """
    ทดสอบการเชื่อมต่อ RTSP ของกล้องรายตัวแบบ On-Demand
    อัปเดต verification_status เป็น 'verified' หรือ 'failed' ลงฐานข้อมูล
    """
    return await admin_service.verify_admin_camera(camera_id=camera_id, db=db, admin=admin)


@router.post("/cameras/verify-all", response_model=schemas.CameraBatchVerificationResponse)
async def verify_all_admin_cameras(
    payload: Optional[schemas.CameraVerifyBatchRequest] = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """
    ทดสอบการเชื่อมต่อ RTSP ของกล้องแบบ On-Demand (Concurrency สูงสุด 5 ตัวพร้อมกัน)
    สามารถระบุ camera_ids เฉพาะกลุ่มที่ต้องการ หรือถ้าไม่ระบุจะตรวจสอบกล้องทั้งหมดในระบบ
    อัปเดต verification_status ลงฐานข้อมูล
    """
    return await admin_service.verify_all_admin_cameras(payload=payload, db=db, admin=admin)


@router.get("/users", response_model=schemas.PaginatedResponse[schemas.UserAdminResponse])
async def list_users(
    user_id: Optional[str] = Query(
        default=None,
        description="กรองเฉพาะ user ที่มี ID ตรงกับค่านี้เท่านั้น (exact match) — ไม่ระบุ = ดูทั้งหมด",
    ),
    email: Optional[str] = Query(
        default=None,
        description="กรองเฉพาะ user ที่อีเมลมีข้อความนี้อยู่ (partial, case-insensitive) — ไม่ระบุ = ดูทั้งหมด",
    ),
    order: Optional[str] = Query(default="desc", pattern="^(asc|desc)$"),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.list_users(
        user_id=user_id,
        email=email,
        order=order,
        page_params=page_params,
        db=db,
        admin=admin,
    )


@router.get("/users/{user_id}", response_model=schemas.UserAdminDetailResponse)
async def get_user_detail(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """ดูรายละเอียด user คนเดียว พร้อมจำนวน webhook/camera ที่มี, ประวัติคำขอใช้งานระบบ
    (access_requests) ทั้งหมดที่เคยส่ง เรียงจากล่าสุดไปเก่าสุด — ใช้โชว์ข้อมูลที่ user กรอกตอน
    สมัครขอใช้งาน (องค์กร/ผู้ติดต่อ/วัตถุประสงค์) ในโมดัล "รายละเอียดผู้ใช้" ฝั่ง admin

    [Contacts]: เพิ่ม contacts — ข้อมูลติดต่อส่วนตัวที่ user กรอกเองผ่าน /my/contacts (facebook/
    line/เบอร์โทร/ฯลฯ) ให้ admin เห็นประกอบการพิจารณาด้วย เป็น read-only ฝั่งนี้ (แก้ไม่ได้จากฝั่ง
    admin — ต้องให้ user แก้ไขเองผ่าน /my/contacts เท่านั้น)"""
    return await admin_service.get_user_detail(user_id=user_id, db=db, admin=admin)


@router.patch("/users/{user_id}/suspend", response_model=schemas.UserAdminResponse)
async def set_user_suspend_status(
    user_id: str,
    payload: schemas.UserSuspendUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """ระงับ/ปลดระงับ user — ห้ามแตะบัญชีของตัวเอง (กัน admin ล็อกตัวเองไม่ได้ตั้งใจ)"""
    return await admin_service.set_user_suspend_status(
        user_id=user_id,
        payload=payload,
        ip_address=request.client.host,
        db=db,
        admin=admin,
    )


@router.get("/webhooks", response_model=schemas.PaginatedResponse[schemas.WebhookAdminResponse])
async def list_webhooks(
    user_id: Optional[str] = Query(default=None, description="กรองเฉพาะ webhook ของ user คนนี้"),
    user_email: Optional[str] = Query(
        default=None,
        description="กรองเฉพาะ webhook ของเจ้าของที่อีเมลมีข้อความนี้อยู่ (partial, case-insensitive)",
    ),
    order: Optional[str] = Query(default="desc", pattern="^(asc|desc)$"),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.list_webhooks(
        user_id=user_id,
        user_email=user_email,
        order=order,
        page_params=page_params,
        db=db,
        admin=admin,
    )


@router.patch("/webhooks/{webhook_id}/status", response_model=schemas.WebhookAdminResponse)
async def set_webhook_status(
    webhook_id: str,
    payload: schemas.WebhookStatusUpdate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.set_webhook_status(
        webhook_id=webhook_id,
        payload=payload,
        background_tasks=background_tasks,
        ip_address=request.client.host,
        db=db,
        admin=admin,
    )


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook_by_admin(
    webhook_id: str,
    payload: schemas.WebhookAdminDeleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.delete_webhook_by_admin(
        webhook_id=webhook_id,
        payload=payload,
        ip_address=request.client.host,
        db=db,
        admin=admin,
    )


@router.get("/audit-log", response_model=schemas.PaginatedResponse[schemas.AdminAuditLogResponse])
async def list_admin_audit_log(
    actor_id: Optional[str] = Query(
        default=None,
        description=(
            "กรองเฉพาะรายการที่ทำโดยผู้ใช้/แอดมินคนนี้ (exact match) — ไม่มีผลกับรายการที่ "
            "actor_type='system' เพราะเป็น background job ไม่มี actor_id ผูกด้วย (เป็น None เสมอ)"
        ),
    ),
    action: Optional[str] = Query(default=None, description="กรองตาม action เช่น 'user.suspend', 'webhook.disable' (exact match)"),
    target_type: Optional[str] = Query(default=None, description="กรองตามประเภทเป้าหมาย: user / webhook_endpoint / access_request / camera"),
    target_id: Optional[str] = Query(default=None, description="กรองตาม target_id (exact match)"),
    order: Optional[str] = Query(default="desc", pattern="^(asc|desc)$"),
    page_params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    return await admin_service.list_admin_audit_log(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        order=order,
        page_params=page_params,
        db=db,
        admin=admin,
    )
