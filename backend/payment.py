"""
ClipMind 支付网关抽象层
=======================
当前实现: XorPay (支持微信/支付宝扫码)
接入新网关: 实现 create_payment / query_order 两个函数即可
"""
import hashlib
import logging

import httpx

from .config import XORPAY_APP_ID, XORPAY_APP_SECRET

logger = logging.getLogger(__name__)

XORPAY_API = "https://xorpay.com/api"
TIMEOUT = 15  # 秒


# ── 内部工具 ──

def _md5_sign(*parts: str) -> str:
    """XorPay 签名: 纯 value 拼接后 MD5"""
    raw = "".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ── 公共接口 ──

def create_native_payment(
    order_id: str,
    price_yuan: float,
    title: str = "ClipMind 余额充值",
    expire_seconds: int = 7200,
) -> dict:
    """创建 NATIVE 扫码支付 → 返回支付二维码链接

    Args:
        order_id: 平台订单号 (需唯一)
        price_yuan: 金额（元）
        title: 商品名称
        expire_seconds: 过期秒数 (默认 2h)

    Returns:
        {"status": "ok", "qr_url": "...", "aoid": "...", "expires_in": N}
        {"status": "error", "message": "..."}  失败时
    """
    if not XORPAY_APP_ID or not XORPAY_APP_SECRET:
        return {"status": "error", "message": "支付网关未配置 (XORPAY_APP_ID / XORPAY_APP_SECRET)"}

    price = f"{price_yuan:.2f}"
    pay_type = "native"
    notify_url = "https://clipmind.local/pay/callback"  # 不会被实际回调（本地无公网IP），依赖轮询

    sign = _md5_sign(title, pay_type, price, order_id, notify_url, XORPAY_APP_SECRET)

    payload = {
        "name": title,
        "pay_type": pay_type,
        "price": price,
        "order_id": order_id,
        "notify_url": notify_url,
        "expire": str(expire_seconds),
        "sign": sign,
    }

    try:
        resp = httpx.post(
            f"{XORPAY_API}/pay/{XORPAY_APP_ID}",
            data=payload,
            timeout=TIMEOUT,
        )
        data = resp.json()
    except Exception as e:
        logger.error(f"XorPay create_payment 请求失败: {e}")
        return {"status": "error", "message": f"支付网关请求失败: {e}"}

    if data.get("status") != "ok":
        logger.warning(f"XorPay 返回异常: {data}")
        return {"status": "error", "message": f"支付网关返回: {data.get('status', '未知错误')}"}

    return {
        "status": "ok",
        "qr_url": data["info"]["qr"],
        "aoid": data.get("aoid", ""),
        "expires_in": data.get("expires_in", expire_seconds),
    }


def query_order(order_id: str) -> dict:
    """查询订单状态（通过平台订单号）

    Returns:
        {"status": "new" | "payed" | "success" | "expire" | "not_exist", ...}
        当 status 为 "payed" 或 "success" 时表示已支付
    """
    if not XORPAY_APP_ID or not XORPAY_APP_SECRET:
        return {"status": "error", "message": "支付网关未配置"}

    sign = _md5_sign(order_id, XORPAY_APP_SECRET)

    try:
        resp = httpx.get(
            f"{XORPAY_API}/query2/{XORPAY_APP_ID}",
            params={"order_id": order_id, "sign": sign},
            timeout=TIMEOUT,
        )
        return resp.json()
    except Exception as e:
        logger.error(f"XorPay query_order 请求失败: {e}")
        return {"status": "error", "message": str(e)}
