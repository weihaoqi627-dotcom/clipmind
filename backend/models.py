"""
SQLAlchemy 数据模型
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, BigInteger, Float, DateTime, Enum as SAEnum
from sqlalchemy import ForeignKey, Text, Index
from sqlalchemy.orm import relationship
import enum

from .database import Base
from .config import FREE_TIER_NEW_USER, FREE_CONCURRENCY


def _utcnow():
    return datetime.now(timezone.utc)


def _new_id():
    return uuid.uuid4().hex[:12]


# ── 枚举 ──

class Membership(str, enum.Enum):
    FREE = "free"
    PREMIUM = "premium"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class BillingType(str, enum.Enum):
    FREE_TIER = "free"       # 免费额度
    PREPAID = "prepaid"      # 预付费


# ── 用户 ──

class User(Base):
    __tablename__ = "users"

    id = Column(String(12), primary_key=True, default=_new_id)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(100), default="")
    status = Column(SAEnum(UserStatus), default=UserStatus.ACTIVE, nullable=False)
    membership = Column(SAEnum(Membership), default=Membership.FREE, nullable=False)

    # Token
    free_tier_remaining = Column(BigInteger, default=FREE_TIER_NEW_USER, nullable=False)
    prepaid_tokens = Column(BigInteger, default=0, nullable=False)

    # 并发限制
    concurrency_limit = Column(Integer, default=FREE_CONCURRENCY, nullable=False)

    # 免费额度月重置
    last_free_reset_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    # 关系
    consumption = relationship("TokenConsumption", back_populates="user", lazy="dynamic")

    @property
    def total_remaining(self) -> int:
        return self.free_tier_remaining + self.prepaid_tokens


# ── Token 消耗记录 ──

class TokenConsumption(Base):
    __tablename__ = "token_consumption"

    id = Column(String(12), primary_key=True, default=_new_id)
    user_id = Column(String(12), ForeignKey("users.id"), nullable=False, index=True)
    model = Column(String(100), nullable=False)
    tokens_in = Column(Integer, default=0, nullable=False)
    tokens_out = Column(Integer, default=0, nullable=False)
    tokens_total = Column(Integer, default=0, nullable=False)
    cost_yuan = Column(Float, default=0.0, nullable=False)
    billing_type = Column(SAEnum(BillingType), nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    api_key_suffix = Column(String(8), default="")   # 用于区分不同 API Key 的消耗（可选）

    user = relationship("User", back_populates="consumption")

    __table_args__ = (
        Index("idx_consumption_user_time", "user_id", "created_at"),
    )


# ── 订单 ──

class Order(Base):
    __tablename__ = "orders"

    id = Column(String(12), primary_key=True, default=_new_id)
    user_id = Column(String(12), ForeignKey("users.id"), nullable=False, index=True)
    amount_yuan = Column(Float, nullable=False)
    tokens_granted = Column(BigInteger, nullable=False)
    status = Column(String(20), default="paid", nullable=False)  # pending / paid / refunded
    remark = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    paid_at = Column(DateTime, nullable=True)


# ── 密码重置验证码 ──

class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id = Column(String(12), primary_key=True, default=_new_id)
    email = Column(String(255), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Integer, default=0, nullable=False)  # 0=未使用, 1=已使用
    created_at = Column(DateTime, default=_utcnow, nullable=False)


