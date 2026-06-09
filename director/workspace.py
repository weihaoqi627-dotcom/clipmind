"""
Workspace 路径管理 — 固定、可预测的目录结构
===============================================

铁律：
  - 所有路径从此派生，零扫描、零猜测
  - 没有 fallback 链，没有"找最近修改目录"
  - 打包后依然工作，不依赖 __file__ 的相对位置

目录结构:
  {CLIPMIND_WORKSPACE}/
  └── projects/
      └── {project_name}/
          ├── pipeline_state.json
          ├── segments_compressed/    ← 压缩后供 VL 分析的片段
          ├── segments/               ← 提取的物理片段
          └── output/                 ← 最终成片

配置方式（按优先级）:
  1. 环境变量 CLIPMIND_WORKSPACE
  2. 默认: 项目根目录下的 workspace/
"""
import os
import re


def _sanitize_path_name(name: str) -> str:
    """将项目名转换为安全的文件/目录名."""
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    safe = safe.strip(". ")
    return safe or "untitled"


def get_workspace_root() -> str:
    """获取 workspace 根目录（固定，不因项目变化而变）"""
    root = os.environ.get("CLIPMIND_WORKSPACE", "")
    if not root:
        # 默认:项目根目录下的 workspace/
        # 当前文件在 director/workspace.py，项目根在 director/..
        root = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workspace")
        )
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    return root


def get_project_dir(project_name: str = "default") -> str:
    """获取项目工作目录（稳定、可预测）"""
    safe = _sanitize_path_name(project_name) if project_name else "default"
    project_dir = os.path.join(get_workspace_root(), "projects", safe)
    os.makedirs(project_dir, exist_ok=True)
    # 创建标准子目录
    for sub in ("segments_compressed", "segments", "output"):
        os.makedirs(os.path.join(project_dir, sub), exist_ok=True)
    return project_dir


def get_active_project_dir() -> str:
    """获取当前活动项目目录。

    所有工具通过此函数找到当前项目，不扫描、不猜测。
    Pipeline 初始化时设置 CLIPMIND_PIPELINE_DIR 环境变量，
    子线程（ThreadPoolExecutor 等）继承该变量。
    """
    env_dir = os.environ.get("CLIPMIND_PIPELINE_DIR", "")
    if env_dir and os.path.isdir(env_dir):
        return env_dir
    raise RuntimeError(
        "未找到活动项目。请先调用 start_project 创建项目，"
        "或设置 CLIPMIND_PIPELINE_DIR 环境变量指向项目目录。\n"
        "（说明：此变量由 Pipeline 初始化时自动设置，手动设置仅用于调试。）"
    )
