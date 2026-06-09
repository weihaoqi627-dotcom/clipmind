"""
Token 计量核心逻辑 — 扣减余额 + 记录消耗
=======================================
proxy_router 和 usage/report 接口都从这里导入

allow_debt=True 时允许余额为负（用于直连上报模式）
"""
from sqlalchemy.orm import Session

from .models import User, TokenConsumption, BillingType


def record_consumption(
    db: Session,
    user: User,
    model: str,
    tokens_in: int,
    tokens_out: int,
    allow_debt: bool = False,
):
    """记录 token 消耗并扣减余额

    Args:
        allow_debt: 允许透支（直连上报用，先消费后扣费）
    """
    tokens_total = tokens_in + tokens_out
    if tokens_total <= 0:
        return

    # 计算成本（百炼价格，参考值）
    cost_input = tokens_in * 0.000001   # 约 1元/百万
    cost_output = tokens_out * 0.000004  # 约 4元/百万
    cost_yuan = round(cost_input + cost_output, 6)

    # 扣减顺序：先免费额度，再预付费
    billing_type = BillingType.FREE_TIER
    if user.free_tier_remaining >= tokens_total:
        user.free_tier_remaining -= tokens_total
    else:
        remaining = tokens_total - user.free_tier_remaining
        user.free_tier_remaining = 0
        if allow_debt:
            # 直连上报模式：允许欠费
            user.prepaid_tokens -= remaining
        else:
            # 代理模式：不能为负
            user.prepaid_tokens = max(0, user.prepaid_tokens - remaining)
        billing_type = BillingType.PREPAID

    consumption = TokenConsumption(
        user_id=user.id,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_total=tokens_total,
        cost_yuan=cost_yuan,
        billing_type=billing_type,
    )
    db.add(consumption)

    try:
        db.commit()
    except Exception:
        db.rollback()
