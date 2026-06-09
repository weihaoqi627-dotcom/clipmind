"""
SMTP 邮件发送工具
=================
用于忘记密码验证码等场景。
"""
import smtplib
import ssl
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS


def generate_code(length: int = 6) -> str:
    """生成纯数字验证码"""
    return "".join(random.choices(string.digits, k=length))


def send_reset_code(to_email: str, code: str) -> bool:
    """
    发送密码重置验证码邮件。
    返回 True 表示发送成功，False 表示失败。
    """
    subject = "ClipMind 密码重置"
    body = f"""
<div style="max-width:480px;margin:0 auto;font-family:'Segoe UI',sans-serif;padding:24px;border:1px solid #e0e0e0;border-radius:12px;">
  <h2 style="margin:0 0 16px;color:#333;">🔐 ClipMind 密码重置</h2>
  <p style="color:#555;font-size:14px;line-height:1.6;">
    您收到了这封邮件是因为有人请求重置您的 ClipMind 账号密码。<br>
    如果这不是您本人操作，请忽略此邮件。
  </p>
  <div style="background:#f5f3ff;border-radius:8px;padding:20px;text-align:center;margin:20px 0;">
    <div style="font-size:32px;letter-spacing:8px;font-weight:700;color:#7C3AED;">{code}</div>
    <div style="font-size:12px;color:#999;margin-top:8px;">验证码有效期为 10 分钟</div>
  </div>
  <p style="color:#999;font-size:12px;">ClipMind 剪意 · AI 视频剪辑桌面应用</p>
</div>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg.attach(MIMEText(body, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())
        return True
    except smtplib.SMTPAuthenticationError:
        print(f"[Email] SMTP 认证失败: {SMTP_USER}")
        return False
    except smtplib.SMTPException as e:
        print(f"[Email] SMTP 发送失败: {e}")
        return False
    except OSError as e:
        print(f"[Email] 网络错误: {e}")
        return False
