"""
用户注册 / 登录 / 密码重置
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, UserStatus, PasswordResetCode
from ..schemas import (
    RegisterRequest, LoginRequest, TokenResponse, UserInfo,
    ForgotPasswordRequest, ResetPasswordRequest,
)
from ..auth import hash_password, verify_password, create_access_token
from ..config import FREE_TIER_NEW_USER, FREE_CONCURRENCY
from ..email_util import generate_code, send_reset_code

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if not req.email or not req.password:
        raise HTTPException(400, "邮箱和密码不能为空")

    if len(req.password) < 6:
        raise HTTPException(400, "密码至少6位")

    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(409, "该邮箱已注册")

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        display_name=req.display_name or req.email.split("@")[0],
        free_tier_remaining=FREE_TIER_NEW_USER,
        concurrency_limit=FREE_CONCURRENCY,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserInfo.model_validate(user),
    )


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        raise HTTPException(401, "邮箱或密码错误")

    if user.status == UserStatus.DISABLED:
        raise HTTPException(403, "账户已被禁用，请联系管理员")

    if not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserInfo.model_validate(user),
    )


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """发送验证码到用户邮箱"""
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        # 不暴露邮箱是否存在，返回模糊成功
        return {"message": "如果该邮箱已注册，验证码已发送"}

    if user.status == UserStatus.DISABLED:
        raise HTTPException(403, "账户已被禁用")

    # 生成 6 位验证码，有效期 10 分钟
    code = generate_code(6)
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    reset = PasswordResetCode(
        email=req.email,
        code=code,
        expires_at=expires,
    )
    db.add(reset)
    db.commit()

    ok = send_reset_code(req.email, code)
    if not ok:
        raise HTTPException(500, "邮件发送失败，请检查邮箱地址或稍后再试")

    return {"message": "如果该邮箱已注册，验证码已发送"}


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """验证验证码并重置密码"""
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        raise HTTPException(404, "用户不存在")

    if user.status == UserStatus.DISABLED:
        raise HTTPException(403, "账户已被禁用")

    # 查找未使用、未过期的验证码
    now = datetime.now(timezone.utc)
    reset = (
        db.query(PasswordResetCode)
        .filter(
            PasswordResetCode.email == req.email,
            PasswordResetCode.code == req.code,
            PasswordResetCode.used == 0,
            PasswordResetCode.expires_at > now,
        )
        .order_by(PasswordResetCode.created_at.desc())
        .first()
    )
    if not reset:
        raise HTTPException(400, "验证码无效或已过期")

    # 标记已使用
    reset.used = 1

    # 更新密码
    user.password_hash = hash_password(req.new_password)
    db.commit()

    return {"message": "密码重置成功，请使用新密码登录"}
