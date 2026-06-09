#!/usr/bin/env python3
"""
ClipMind 导入验证脚本
======================
在 CI 中运行，确保所有关键的第三方包都能正常导入。
依赖已通过 pip install -r requirements.txt + 额外包 安装。
"""
import sys
import importlib

PACKAGES = {
    # ── 后端 ──
    "fastapi": "backend FastAPI 服务",
    "uvicorn": "backend HTTP 服务",
    "sqlalchemy": "ORM 数据库",
    "jose": "JWT 认证",
    "passlib": "密码哈希",
    "httpx": "HTTP 客户端",
    "multipart": "文件上传解析",
    "pydantic": "数据校验",
    "pydantic_settings": "配置管理",

    # ── RPC 管线 ──
    "openai": "LLM 对话 (pipeline + tools)",
    "PIL": "图像处理 (colors/face)",
    "numpy": "数值计算 (colors/face)",
    "cv2": "视频/人脸跟踪 (track/face)",
    "dashscope": "阿里云 VL 视觉模型",
    "requests": "HTTP 请求 (analyze/watch/audio_prospect)",
    "curl_cffi": "网页搜索 (web_search)",
    "uniface": "人脸检测追踪 (face)",

    # ── 间接依赖 ──
    "aiohttp": "dashscope 异步 HTTP",
    "cryptography": "加密运算",
    "bcrypt": "密码哈希算法",
    "yaml": "配置序列化",
    "tqdm": "进度条",
    "rich": "终端美化 (dashscope)",
}

errors = []

for name, desc in PACKAGES.items():
    try:
        importlib.import_module(name)
        print(f"  ✅ {name:30s} → {desc}")
    except ImportError as e:
        print(f"  ❌ {name:30s} → {desc}: {e}")
        errors.append(name)

if errors:
    print(f"\n❌ {len(errors)} 个包导入失败: {', '.join(errors)}")
    sys.exit(1)
else:
    print(f"\n✅ 全部 {len(PACKAGES)} 个包导入成功")
