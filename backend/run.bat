@echo off
chcp 65001 > nul
cd /d "%~dp0.."
echo ╔══════════════════════════════════════╗
echo ║     ClipMind Backend Server         ║
echo ╚══════════════════════════════════════╝
echo.
echo 端口: 8765
echo 管理后台: http://localhost:8765/admin
echo API文档: http://localhost:8765/docs
echo.

:: 设置环境变量（按需修改）
set CLIPMIND_JWT_SECRET=
set DASHSCOPE_API_KEY=
set CLIPMIND_ADMIN_USER=admin
set CLIPMIND_ADMIN_PASS=admin123

:: XorPay 支付网关（注册: https://xorpay.com）
set XORPAY_APP_ID=
set XORPAY_APP_SECRET=

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765 --reload

pause
