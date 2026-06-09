"""
ClipMind Backend — HTTP API 服务
=================================
桌面端认证 + AI API 代理 + Token 计量 + 支付 + 管理后台

启动:  uvicorn backend.main:app --host 0.0.0.0 --port 8765
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import init_db, Base, engine, SessionLocal
from .config import ADMIN_USERNAME, ADMIN_PASSWORD

from .routers import auth_router, user_router, proxy_router, admin_router, payment_router

logger = logging.getLogger(__name__)

# ── 加载 .env ──
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(str(_env_path))
        print(f"[Backend] .env 已加载: {_env_path}")
    except ImportError:
        print("[Backend] python-dotenv 未安装,跳过 .env 加载")


# ── 后台扫描: 自动处理已支付但因网络问题未到账的订单 ──
async def _sweep_pending_orders():
    """每 60 秒扫描所有 pending 订单，查 XorPay 状态，自动到账"""
    while True:
        await asyncio.sleep(60)
        try:
            from .models import Order, User
            from .payment import query_order

            db = SessionLocal()
            try:
                pending = db.query(Order).filter(Order.status == "pending").all()
                now = datetime.now(timezone.utc)
                for order in pending:
                    # 跳过 2 分钟内的新订单（给桌面端轮询机会）
                    age = (now - order.created_at).total_seconds()
                    if age < 120:
                        continue

                    result = query_order(order.id)
                    xstatus = result.get("status", "new")
                    if xstatus in ("payed", "success"):
                        user = db.query(User).filter(User.id == order.user_id).first()
                        if user:
                            user.prepaid_tokens += order.tokens_granted
                        order.status = "paid"
                        order.paid_at = now
                        db.commit()
                        logger.info(f"后台扫描: 订单 {order.id} 自动到账 {order.tokens_granted} token")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[PaymentSweep] 扫描异常: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sweep = asyncio.create_task(_sweep_pending_orders())
    yield
    sweep.cancel()


app = FastAPI(
    title="ClipMind Backend",
    version="1.0.0",
    description="ClipMind AI 视频剪辑后端服务",
    lifespan=lifespan,
)

# ── CORS（桌面端访问） ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由 ──
app.include_router(auth_router.router)
app.include_router(user_router.router)
app.include_router(proxy_router.router)
app.include_router(admin_router.router)
app.include_router(payment_router.router)


# ── 管理后台静态页面 ──
_here = Path(__file__).parent
admin_static = _here / "admin"
if admin_static.exists():
    app.mount("/admin", StaticFiles(directory=str(admin_static), html=True), name="admin")


@app.get("/")
def root():
    return {"service": "ClipMind Backend", "status": "running", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}
