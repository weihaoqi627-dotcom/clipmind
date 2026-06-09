"""
管理后台 API — 仅管理员可访问
用法:
  curl -u admin:<password> http://localhost:8765/api/admin/users
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import User, TokenConsumption, Order, UserStatus, Membership
from ..schemas import AdminUserItem, AdminAdjustRequest
from ..config import ADMIN_USERNAME, ADMIN_PASSWORD, PREMIUM_CONCURRENCY

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _verify_admin(x_admin_auth: str = Header("")):
    """简单 Basic Auth 校验"""
    import base64
    if not x_admin_auth:
        raise HTTPException(401, "需要认证")
    try:
        decoded = base64.b64decode(x_admin_auth).decode()
        username, password = decoded.split(":", 1)
    except Exception:
        raise HTTPException(401, "认证格式错误")
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        raise HTTPException(401, "用户名或密码错误")


@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    _=Depends(_verify_admin),
):
    """总览统计"""
    total_users = db.query(func.count(User.id)).scalar()
    active_users = db.query(func.count(User.id)).filter(User.status == UserStatus.ACTIVE).scalar()
    premium_users = db.query(func.count(User.id)).filter(User.membership == Membership.PREMIUM).scalar()

    total_consumption = (
        db.query(func.sum(TokenConsumption.tokens_total))
        .filter(TokenConsumption.created_at >= datetime.now(timezone.utc) - timedelta(days=30))
        .scalar() or 0
    )

    total_revenue = (
        db.query(func.sum(Order.amount_yuan))
        .filter(Order.status == "paid")
        .scalar() or 0.0
    )

    return {
        "total_users": total_users,
        "active_users": active_users,
        "premium_users": premium_users,
        "monthly_tokens_consumed": total_consumption,
        "total_revenue_yuan": round(total_revenue, 2),
    }


@router.get("/users")
def list_users(
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    _=Depends(_verify_admin),
):
    """用户列表"""
    total = db.query(func.count(User.id)).scalar()
    users = db.query(User).order_by(User.created_at.desc()).offset((page - 1) * size).limit(size).all()

    return {
        "total": total,
        "page": page,
        "size": size,
        "users": [AdminUserItem.model_validate(u) for u in users],
    }


@router.post("/users/{user_id}/toggle")
def toggle_user(
    user_id: str,
    db: Session = Depends(get_db),
    _=Depends(_verify_admin),
):
    """启用/禁用用户"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    user.status = UserStatus.DISABLED if user.status == UserStatus.ACTIVE else UserStatus.ACTIVE
    db.commit()
    return {"status": user.status.value}


@router.post("/users/{user_id}/adjust")
def adjust_user(
    user_id: str,
    req: AdminAdjustRequest,
    db: Session = Depends(get_db),
    _=Depends(_verify_admin),
):
    """手动调整用户额度（充值/扣减）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")

    if req.tokens > 0:
        user.prepaid_tokens += req.tokens
        # 同时记录订单
        order = Order(
            user_id=user_id,
            amount_yuan=0.0,
            tokens_granted=req.tokens,
            status="paid",
            remark=req.reason or "管理员手动充值",
        )
        db.add(order)
    elif req.tokens < 0:
        user.prepaid_tokens = max(0, user.prepaid_tokens + req.tokens)
    db.commit()

    return {
        "prepaid_tokens": user.prepaid_tokens,
        "free_tier_remaining": user.free_tier_remaining,
        "total_remaining": user.total_remaining,
    }


@router.post("/users/{user_id}/upgrade")
def upgrade_user(
    user_id: str,
    db: Session = Depends(get_db),
    _=Depends(_verify_admin),
):
    """升级为会员"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    user.membership = Membership.PREMIUM
    user.concurrency_limit = PREMIUM_CONCURRENCY
    db.commit()
    return {"membership": user.membership.value, "concurrency_limit": user.concurrency_limit}


@router.get("/orders")
def list_orders(
    db: Session = Depends(get_db),
    _=Depends(_verify_admin),
):
    """所有订单（含待确认）"""
    orders = (
        db.query(Order, User.email)
        .join(User, Order.user_id == User.id, isouter=True)
        .order_by(Order.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "order_id": o.Order.id,
            "user_id": o.Order.user_id,
            "email": o.email,
            "amount_yuan": o.Order.amount_yuan,
            "tokens_granted": o.Order.tokens_granted,
            "status": o.Order.status,
            "created_at": o.Order.created_at.isoformat(),
            "paid_at": o.Order.paid_at.isoformat() if o.Order.paid_at else None,
        }
        for o in orders
    ]


@router.post("/orders/{order_id}/confirm")
def confirm_order(
    order_id: str,
    db: Session = Depends(get_db),
    _=Depends(_verify_admin),
):
    """确认订单 → 立即到账（管理员确认收款后调用）"""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status != "pending":
        raise HTTPException(400, f"订单状态为 {order.status}，无法确认")

    # 到账
    user = db.query(User).filter(User.id == order.user_id).first()
    user.prepaid_tokens += order.tokens_granted
    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "paid",
        "user_id": order.user_id,
        "amount_yuan": order.amount_yuan,
        "tokens_granted": order.tokens_granted,
    }


@router.get("/usage/report")
def usage_report(
    days: int = 7,
    db: Session = Depends(get_db),
    _=Depends(_verify_admin),
):
    """用量日报"""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # 按天、按模型统计
    rows = (
        db.query(
            func.date(TokenConsumption.created_at).label("day"),
            TokenConsumption.model,
            func.sum(TokenConsumption.tokens_total).label("tokens"),
            func.sum(TokenConsumption.cost_yuan).label("cost"),
        )
        .filter(TokenConsumption.created_at >= since)
        .group_by(func.date(TokenConsumption.created_at), TokenConsumption.model)
        .order_by("day")
        .all()
    )

    # 用户排行（总消耗）
    top_users = (
        db.query(
            TokenConsumption.user_id,
            User.email,
            User.display_name,
            func.sum(TokenConsumption.tokens_total).label("tokens"),
        )
        .join(User, TokenConsumption.user_id == User.id)
        .filter(TokenConsumption.created_at >= since)
        .group_by(TokenConsumption.user_id)
        .order_by(func.sum(TokenConsumption.tokens_total).desc())
        .limit(20)
        .all()
    )

    return {
        "daily": [
            {"day": str(r.day), "model": r.model, "tokens": int(r.tokens), "cost_yuan": round(r.cost, 4)}
            for r in rows
        ],
        "top_users": [
            {"user_id": r.user_id, "email": r.email, "name": r.display_name, "tokens": int(r.tokens)}
            for r in top_users
        ],
    }
