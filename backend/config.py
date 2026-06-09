"""
ClipMind Backend 配置
======================
安全敏感配置从环境变量读取 + 内嵌加密密钥（打包时加密，运行时解密）。
"""
import os
import base64
from pathlib import Path

# ── 路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "backend_data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "clipmind.db"

# ── JWT ──
SECRET_KEY = os.environ.get("CLIPMIND_JWT_SECRET", "")
if not SECRET_KEY:
    # 开发模式用随机 key，重启后所有 token 失效
    import secrets
    SECRET_KEY = secrets.token_hex(32)
    print("[Backend] WARN: CLIPMIND_JWT_SECRET not set, using random key (logins invalid after restart)")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 天

# ── 内嵌密钥解密 ──
# XOR + base64 加密，防止 strings/解包直接提取
_EMBEDDED_XOR_KEY = b"ClipMind_S3cret!"
_EMBEDDED_API_KEY = "MAdEQCwMWlVsNwsHFFwRFXENXkh6DQ8HPmJWAUVSEBB0XQ0="

def _decrypt_embedded() -> str:
    """解密打包时内嵌的 API Key"""
    try:
        data = base64.b64decode(_EMBEDDED_API_KEY)
        dec = bytes(data[i] ^ _EMBEDDED_XOR_KEY[i % len(_EMBEDDED_XOR_KEY)] for i in range(len(data)))
        return dec.decode()
    except Exception:
        return ""

# ── 百炼代理 ──
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"
# 优先环境变量（.env 加载后注入），没有则解内嵌加密 key
ENTERPRISE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "") or _decrypt_embedded()
if not ENTERPRISE_API_KEY:
    print("[Backend] WARN: DASHSCOPE_API_KEY not set, proxy unavailable")

# ── 免费额度 ──
FREE_TIER_NEW_USER = 5_000_000       # 500万 token
FREE_TIER_MONTHLY = 1_000_000        # 每月 100万
PREMIUM_CONCURRENCY = 20
FREE_CONCURRENCY = 5

# ── 充值定价（1 元 = 多少 token） ──
TOKEN_PRICE_PER_YUAN = 500_000  # 1元 = 50万 token

# ── 管理后台密码（简单认证） ──
ADMIN_USERNAME = os.environ.get("CLIPMIND_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("CLIPMIND_ADMIN_PASS", "")
if not ADMIN_PASSWORD:
    print("[Backend] WARN: CLIPMIND_ADMIN_PASS not set, using default password")
    ADMIN_PASSWORD = "wei106614"

# ── XorPay 支付网关 ──
# 注册: https://xorpay.com  →  获取 APP ID 和 APP SECRET
XORPAY_APP_ID = os.environ.get("XORPAY_APP_ID", "")
XORPAY_APP_SECRET = os.environ.get("XORPAY_APP_SECRET", "")
if not XORPAY_APP_ID:
    print("[Backend] WARN: XORPAY_APP_ID not set, 支付功能不可用")

# ── SMTP 邮件（忘记密码） ──
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.163.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "wei13772041720@163.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "RVYumNXM2xEJAace")
