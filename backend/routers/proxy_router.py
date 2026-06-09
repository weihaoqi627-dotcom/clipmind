"""
AI API 代理 — 将桌面端的百炼请求转发，同时计量 token 消耗

桌面端改 base_url:
  原来的: https://dashscope.aliyuncs.com/compatible-mode/v1
  改成:   http://<后端地址>/api/proxy/compatible-mode/v1

桌面端的 api_key 改成用户的 JWT token（或者 Bearer token 模式）。
"""
import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, TokenConsumption, Membership, BillingType
from ..config import DASHSCOPE_BASE_URL, ENTERPRISE_API_KEY, PREMIUM_CONCURRENCY, FREE_TIER_MONTHLY
from ..billing import record_consumption
from .deps import get_current_user

router = APIRouter(prefix="/api/proxy", tags=["proxy"])


def _check_monthly_reset(user: User, db: Session):
    """每月免费额度自动重置（懒加载：每次调用时检查）"""
    now = datetime.now(timezone.utc)
    if user.last_free_reset_at is None:
        # 首次注册时已经给了 FREE_TIER_NEW_USER，只记时间不重复给
        user.last_free_reset_at = now
        db.commit()
        return

    # 距离上次重置超过 30 天，补 100 万
    if now - user.last_free_reset_at > timedelta(days=30):
        user.free_tier_remaining += FREE_TIER_MONTHLY
        user.last_free_reset_at = now
        db.commit()


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_all(
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通用代理：把 /api/proxy/{path} 转发到 https://dashscope.aliyuncs.com/{path}"""
    if not ENTERPRISE_API_KEY:
        raise HTTPException(503, "后端百炼 API Key 未配置，AI 服务不可用")

    # 每月免费额度重置（懒加载）
    _check_monthly_reset(user, db)
    if user.total_remaining <= 0:
        raise HTTPException(402, "Token 额度已用完，请充值")

    # 构建目标 URL（带上 query string）
    query = request.url.query
    if query:
        target_url = f"{DASHSCOPE_BASE_URL}/{path}?{query}"
    else:
        target_url = f"{DASHSCOPE_BASE_URL}/{path}"

    # 读取请求体
    body = await request.body()

    # 判断是否为流式请求（看请求体里有没有 stream=true）
    is_stream = False
    if body:
        try:
            req_body = json.loads(body)
            is_stream = req_body.get("stream", False)
        except json.JSONDecodeError:
            req_body = None
    else:
        req_body = None

    headers = {
        "Authorization": f"Bearer {ENTERPRISE_API_KEY}",
        "Content-Type": request.headers.get("Content-Type", "application/json"),
    }
    # 去掉 host / content-length / content-encoding（httpx 自己处理）
    for skip in ("host", "content-length", "content-encoding", "transfer-encoding"):
        headers.pop(skip, None)

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        if is_stream:
            # ── 流式代理(全量缓冲式) ──
            # 注意:不要用 FastAPI StreamingResponse — uvicorn 在 Windows 下
            # 的 chunked transfer 终止有 bug,导致客户端收到 incomplete chunked read.
            # 替代方案:先缓冲全部 SSE bytes,然后一次性返回。
            full_response_body = b""
            content_type = "text/event-stream"

            async with client.stream(
                request.method,
                target_url,
                headers=headers,
                content=body,
            ) as resp:
                status_code = resp.status_code
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    return JSONResponse(
                        status_code=resp.status_code,
                        content={"error": error_body.decode()[:500]},
                    )

                async for chunk in resp.aiter_bytes():
                    full_response_body += chunk

            # 解析 usage 并记录
            usage = _extract_stream_usage(full_response_body)
            if usage:
                tokens_in = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
                tokens_out = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
                _record_consumption(db, user, req_body, tokens_in, tokens_out)

            return Response(
                content=full_response_body,
                media_type=content_type,
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        else:
            # ── 非流式 ──
            resp = await client.request(
                request.method,
                target_url,
                headers=headers,
                content=body,
            )
            resp_body = await resp.aread()

            if resp.status_code != 200:
                return JSONResponse(
                    status_code=resp.status_code,
                    content={"error": resp_body.decode()[:500]},
                )

            # 解析 usage
            try:
                resp_json = json.loads(resp_body)
                usage = resp_json.get("usage", {})
                tokens_in = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
                tokens_out = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
                if tokens_in or tokens_out:
                    _record_consumption(db, user, req_body, tokens_in, tokens_out)
            except json.JSONDecodeError:
                resp_json = {"raw": resp_body.decode(errors='replace')[:500]}

            return JSONResponse(content=resp_json)


def _extract_stream_usage(raw: bytes) -> dict | None:
    """从 SSE 流式响应中提取最后一个 usage 块"""
    for line in raw.decode().split("\n"):
        line = line.strip()
        if line.startswith("data: ") and "[DONE]" not in line:
            try:
                chunk = json.loads(line[6:])
                usage = chunk.get("usage")
                if usage:
                    return usage
            except json.JSONDecodeError:
                continue
    return None


def _record_consumption(
    db: Session,
    user: User,
    req_body: dict | None,
    tokens_in: int,
    tokens_out: int,
):
    """记录 token 消耗并扣减余额"""
    model = ""
    if req_body:
        model = req_body.get("model", "")
    record_consumption(db, user, model, tokens_in, tokens_out)
