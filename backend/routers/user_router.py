"""
用户信息 / 用量查询 / 充值（桌面端调用的接口）
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import User, TokenConsumption, UserStatus, Order
from ..schemas import UserInfo, UsageQuery, UsageStats, RechargeRequest, UsageReportRequest
from ..config import TOKEN_PRICE_PER_YUAN
from ..billing import record_consumption
from .deps import get_current_user

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return UserInfo.model_validate(user)


@router.get("/balance")
def get_balance(user: User = Depends(get_current_user)):
    """获取剩余额度"""
    return {
        "free_tier_remaining": user.free_tier_remaining,
        "prepaid_tokens": user.prepaid_tokens,
        "total_remaining": user.total_remaining,
        "membership": user.membership.value,
        "concurrency_limit": user.concurrency_limit,
    }


@router.post("/usage")
def get_usage(
    query: UsageQuery,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查询用量历史"""
    q = db.query(TokenConsumption).filter(TokenConsumption.user_id == user.id)

    # 时间范围
    now = datetime.now(timezone.utc)
    if query.time_range == "day":
        since = now - timedelta(days=1)
    elif query.time_range == "week":
        since = now - timedelta(days=7)
    elif query.time_range == "month":
        since = now - timedelta(days=30)
    else:
        since = datetime(2000, 1, 1, tzinfo=timezone.utc)

    q = q.filter(TokenConsumption.created_at >= since)

    if query.model:
        q = q.filter(TokenConsumption.model == query.model)

    records = q.order_by(TokenConsumption.created_at.desc()).limit(1000).all()

    # 汇总
    by_model = (
        db.query(
            TokenConsumption.model,
            func.sum(TokenConsumption.tokens_total).label("tokens"),
            func.sum(TokenConsumption.cost_yuan).label("cost"),
        )
        .filter(
            TokenConsumption.user_id == user.id,
            TokenConsumption.created_at >= since,
        )
        .group_by(TokenConsumption.model)
        .all()
    )

    return UsageStats(
        total_tokens_in=sum(r.tokens_in for r in records),
        total_tokens_out=sum(r.tokens_out for r in records),
        total_tokens=sum(r.tokens_total for r in records),
        total_cost_yuan=round(sum(r.cost_yuan for r in records), 4),
        by_model=[
            {"model": m, "tokens": int(t), "cost_yuan": round(c, 4)}
            for m, t, c in by_model
        ],
        records=[
            {
                "model": r.model,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "tokens_total": r.tokens_total,
                "cost_yuan": round(r.cost_yuan, 4),
                "billing_type": r.billing_type.value,
                "time": r.created_at.isoformat(),
            }
            for r in records[:200]
        ],
    )


@router.post("/usage/report")
def report_usage(
    req: UsageReportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上报 AI 调用消耗（管线直连阿里时调用此接口扣费）"""
    record_consumption(db, user, req.model, req.tokens_in, req.tokens_out)
    return {"status": "ok"}


@router.post("/recharge")
def create_recharge(
    req: RechargeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户发起充值请求（创建待支付订单）"""
    if req.amount_yuan <= 0:
        raise HTTPException(400, "充值金额必须大于 0")

    tokens = int(req.amount_yuan * TOKEN_PRICE_PER_YUAN)

    order = Order(
        user_id=user.id,
        amount_yuan=req.amount_yuan,
        tokens_granted=tokens,
        status="pending",
        remark=req.remark or "",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    return {
        "order_id": order.id,
        "amount_yuan": order.amount_yuan,
        "tokens_granted": order.tokens_granted,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
    }


@router.get("/orders")
def list_orders(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查看自己的充值记录"""
    orders = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "order_id": o.id,
            "amount_yuan": o.amount_yuan,
            "tokens_granted": o.tokens_granted,
            "status": o.status,
            "created_at": o.created_at.isoformat(),
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
        }
        for o in orders
    ]
