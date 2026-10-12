from fastapi import APIRouter, Depends, Request, Response, Cookie, BackgroundTasks, Form
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from smartlpr import models
import smartlpr.schemas as schemas
from smartlpr.database import get_db
from smartlpr.security import (
    get_current_user,
    oauth2_scheme,
)
from services import auth_service
from services.auth_service import REFRESH_TOKEN_COOKIE_NAME

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register")
async def register_user(user: schemas.UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    return await auth_service.register_user(user=user, ip_address=request.client.host, db=db)


@router.post("/verify-otp")
async def verify_otp_endpoint(
    payload: schemas.OtpVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.verify_otp_endpoint(
        payload=payload,
        ip_address=request.client.host,
        db=db,
    )


@router.post("/resend-otp")
async def resend_otp(payload: schemas.OtpResendRequest, request: Request, db: AsyncSession = Depends(get_db)):
    return await auth_service.resend_otp(payload=payload, ip_address=request.client.host, db=db)


@router.post("/login", response_model=schemas.Token)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    remember_me: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.login(
        ip_address=request.client.host,
        response=response,
        form_data=form_data,
        remember_me=remember_me,
        db=db,
    )


@router.post("/refresh", response_model=schemas.Token)
async def refresh_access_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_TOKEN_COOKIE_NAME),
):
    return await auth_service.refresh_access_token(
        ip_address=request.client.host,
        response=response,
        db=db,
        refresh_token=refresh_token,
    )


@router.post("/forgot-password")
async def forgot_password(
    payload: schemas.ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.forgot_password(
        payload=payload,
        ip_address=request.client.host,
        background_tasks=background_tasks,
        db=db,
    )


@router.post("/verify-reset-otp", response_model=schemas.ResetTokenResponse)
async def verify_reset_otp(payload: schemas.VerifyResetOtpRequest, request: Request, db: AsyncSession = Depends(get_db)):
    return await auth_service.verify_reset_otp(
        payload=payload,
        ip_address=request.client.host,
        db=db,
    )


@router.post("/reset-password")
async def reset_password(payload: schemas.ResetPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    return await auth_service.reset_password(
        payload=payload,
        ip_address=request.client.host,
        db=db,
    )


@router.post("/change-password")
async def change_password(
    payload: schemas.ChangePasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme),
    current_user: models.User = Depends(get_current_user),
):
    return await auth_service.change_password(
        payload=payload,
        ip_address=request.client.host,
        db=db,
        token=token,
        current_user=current_user,
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_TOKEN_COOKIE_NAME),
):
    return await auth_service.logout(
        ip_address=request.client.host,
        response=response,
        token=token,
        db=db,
        refresh_token=refresh_token,
    )


@router.get("/me", response_model=schemas.UserMeResponse)
def get_me(current_user: models.User = Depends(get_current_user)):
    return auth_service.get_me(current_user=current_user)


@router.patch("/username", response_model=schemas.UserMeResponse)
async def update_username(
    payload: schemas.UsernameUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """เปลี่ยน username ของตัวเอง — จำกัด 1 ครั้ง/7 วัน (แบบเดียวกับ regenerate API key)
    ยกเว้นครั้งแรกที่ยังไม่เคยตั้งเลย (username เดิมเป็น None — user เก่าก่อนมีฟีเจอร์นี้ หรือ
    DB ที่เพิ่ง migrate คอลัมน์นี้เข้ามาใหม่) ให้ตั้งได้ฟรีไม่ติด lockout ก่อน"""
    return await auth_service.update_username(
        payload=payload,
        ip_address=request.client.host,
        db=db,
        current_user=current_user,
    )


@router.post("/change-email/request")
async def change_email_request(
    payload: schemas.EmailChangeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """ขั้นตอนที่ 1: ตรวจสอบรหัสผ่านปัจจุบัน, ตรวจ cooldown 7 วัน, ตรวจอีเมลซ้ำ
    แล้วส่ง OTP ไปยังอีเมลใหม่เพื่อรอยืนยัน — อีเมลยังไม่ถูกเปลี่ยน ณ จุดนี้"""
    return await auth_service.change_email_request(
        payload=payload,
        background_tasks=background_tasks,
        db=db,
        current_user=current_user,
    )


@router.post("/change-email/verify")
async def change_email_verify(
    payload: schemas.EmailChangeVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """ขั้นตอนที่ 2: ยืนยัน OTP ที่ส่งไปอีเมลใหม่ — ถ้าถูกต้องจะเปลี่ยนอีเมลจริง
    แล้ว revoke ทุก refresh token (บังคับ login ใหม่ทุกอุปกรณ์)"""
    return await auth_service.change_email_verify(
        payload=payload,
        ip_address=request.client.host,
        response=response,
        db=db,
        current_user=current_user,
    )
