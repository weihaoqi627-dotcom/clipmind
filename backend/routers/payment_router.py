"""
支付相关 API — 创建支付 / 轮询状态 / 自动到账
=========================================
流程:
  1. 桌面端 POST /api/payment/create → 创建订单 + 获取支付二维码
  2. 桌面端展示二维码给用户扫码
  3. 桌面端每 3 秒轮询 GET /api/payment/status/{order_id}
  4. 后端查 XorPay → 已支付则自动到账 → 返回 "paid"
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Order
from ..config import TOKEN_PRICE_PER_YUAN
from ..payment import create_native_payment, query_order
from .deps import get_current_user

router = APIRouter(prefix="/api/payment", tags=["payment"])


# ── Schemas ──

class PaymentCreateRequest(BaseModel):
    amount_yuan: float = Field(gt=0, le=99999, description="充值金额（元）")
    pay_type: str = "native"  # 预留: 后续支持 alipay/jsapi 等


class PaymentCreateResponse(BaseModel):
    order_id: str
    qr_url: str
    tokens_granted: int
    amount_yuan: float
    expires_in: int


class PaymentStatusResponse(BaseModel):
    status: str  # "pending" | "paid" | "expired" | "not_found"
    tokens_granted: int = 0
    order_id: str = ""


# ── API ──

@router.post("/create", response_model=PaymentCreateResponse)
def create_payment(
    req: PaymentCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建充值订单 + 获取支付二维码"""
    if not req.amount_yuan > 0:
        raise HTTPException(400, "金额必须大于 0")

    # 1. 创建本地订单
    tokens = int(req.amount_yuan * TOKEN_PRICE_PER_YUAN)
    order = Order(
        user_id=user.id,
        amount_yuan=req.amount_yuan,
        tokens_granted=tokens,
        status="pending",
        remark="",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # 2. 调 XorPay 获取二维码
    result = create_native_payment(
        order_id=order.id,
        price_yuan=req.amount_yuan,
    )

    if result.get("status") != "ok":
        # 支付网关失败，删除订单并报错
        db.delete(order)
        db.commit()
        raise HTTPException(502, f"支付网关错误: {result.get('message', '未知错误')}")

    return PaymentCreateResponse(
        order_id=order.id,
        qr_url=result["qr_url"],
        tokens_granted=tokens,
        amount_yuan=req.amount_yuan,
        expires_in=result.get("expires_in", 7200),
    )


@router.get("/status/{order_id}", response_model=PaymentStatusResponse)
def check_payment(
    order_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """轮询支付状态 — 已支付则自动到账"""
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == user.id,
    ).first()

    if not order:
        # 允许查不到（可能是跨用户查询），但返回 not_found
        return PaymentStatusResponse(status="not_found")

    # 已到账 → 秒回
    if order.status == "paid":
        return PaymentStatusResponse(
            status="paid",
            tokens_granted=order.tokens_granted,
            order_id=order.id,
        )

    # 查 XorPay 状态
    result = query_order(order.id)
    xorpay_status = result.get("status", "new")

    if xorpay_status in ("payed", "success"):
        # ✅ 已支付 → 自动到账
        user = db.query(User).filter(User.id == order.user_id).first()
        if user:
            user.prepaid_tokens += order.tokens_granted
        order.status = "paid"
        order.paid_at = datetime.now(timezone.utc)
        db.commit()

        return PaymentStatusResponse(
            status="paid",
            tokens_granted=order.tokens_granted,
            order_id=order.id,
        )

    elif xorpay_status == "expire":
        return PaymentStatusResponse(status="expired", order_id=order.id)

    else:
        # "new" / "not_exist" / 其他 → 未支付
        return PaymentStatusResponse(status="pending", order_id=order.id)
