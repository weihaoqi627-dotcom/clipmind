"""
Pydantic 请求/响应 schema
"""
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ── Auth ──

class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    display_name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserInfo"


class UserInfo(BaseModel):
    id: str
    email: str
    display_name: str
    membership: str
    status: str
    free_tier_remaining: int
    prepaid_tokens: int
    total_remaining: int
    concurrency_limit: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── 用量 ──

class UsageQuery(BaseModel):
    time_range: str = "month"  # day / week / month / all
    model: str = ""


class UsageStats(BaseModel):
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_tokens: int = 0
    total_cost_yuan: float = 0.0
    by_model: list[dict] = []
    records: list[dict] = []


# ── 管理员 ──

class AdminUserItem(BaseModel):
    id: str
    email: str
    display_name: str
    membership: str
    status: str
    free_tier_remaining: int
    prepaid_tokens: int
    total_remaining: int
    concurrency_limit: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminAdjustRequest(BaseModel):
    tokens: int = 0  # 正数=增加，负数=扣减
    reason: str = ""


class RechargeRequest(BaseModel):
    amount_yuan: float = Field(gt=0, description="充值金额（元）")
    remark: str = ""


class UsageReportRequest(BaseModel):
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=6)
