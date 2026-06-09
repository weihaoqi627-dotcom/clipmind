"""
ClipMind 集中配置管理.

所有模块通过此模块读写配置,不再直接读 config.json.
API key 只从 env 或安全存储读取,不硬编码.
"""

import os
from pathlib import Path

from .exceptions import ConfigError
from .logging_config import get_logger
from .storage import JsonStore

log = get_logger("config")

# ── 路径常量 ──

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 可写数据根目录：优先环境变量 CLIPMIND_DATA_HOME，否则用 PROJECT_ROOT
# 打包模式下必须设 CLIPMIND_DATA_HOME 指向 %APPDATA%/ClipMind 等可写路径
_DATA_HOME = os.environ.get("CLIPMIND_DATA_HOME", "").strip()
if _DATA_HOME:
    _data_root = Path(_DATA_HOME)
else:
    _data_root = PROJECT_ROOT

CONFIG_FILE = _data_root / "config.json"
DATA_DIR = _data_root / "data"
DRAFTS_DIR = _data_root / "drafts"
LOGS_DIR = _data_root / "logs"
OUTPUT_DIR = _data_root / "output"

# ── 受保护目录（禁止任何清理/删除操作触及） ──
PROTECTED_DIRS = [
    "downloads",          # BGM / 音效 / 字体 资源库
    "downloads/music",    # BGM 曲库
    "downloads/sfx",      # 音效库
    "downloads/fonts",    # 字体库
]


def is_protected(path: str | Path) -> bool:
    """检查路径是否属于受保护目录.

    在 any 清理/删除操作前调用此函数,
    返回 True 则禁止操作.
    """
    path = Path(path).resolve()
    for rel in PROTECTED_DIRS:
        protected = (PROJECT_ROOT / rel).resolve()
        if path == protected or protected in path.parents:
            return True
    return False

# ── 模型角色注册表 ──
# 所有工具通过 get_model_for_role() 拿模型名,不再各自硬编码.
# 新增模型角色只需在这里加一项.

MODEL_ROLES = {
    "director": "导演推理(llm_func/agent_loop)",
    "vision": "画面理解(VL分析/prospect/watch/batch_analyze)",
    "audio": "语音识别/分析(audio_prospect/ASR转写)",
}

DEFAULT_MODEL_MAP = {
    "director": "qwen3.6-plus",
    "vision": "qwen3.6-plus",
    "audio": "qwen3-omni-flash",
}


def get_model_for_role(role: str) -> str:
    """获取指定角色使用的模型名.

    Args:
        role: 模型角色名, 取值 MODEL_ROLES 的 key:
              "director" - 导演推理
              "vision"   - 画面理解(VL)
              "audio"    - 语音识别/分析

    Returns:
        模型名, 如 "qwen3.6-plus"
    """
    if role not in MODEL_ROLES:
        raise ConfigError(
            f"未知模型角色 '{role}'. 可用角色: {list(MODEL_ROLES.keys())}",
            code="UNKNOWN_MODEL_ROLE",
        )
    store = _get_store()
    config = store.read()
    model_map = config.get("model_map", {})
    return model_map.get(role, DEFAULT_MODEL_MAP[role])


def set_model_for_role(role: str, model: str) -> None:
    """设置指定角色使用的模型名并持久化.

    Args:
        role: 模型角色名, 取值 MODEL_ROLES 的 key
        model: 模型名, 如 "qwen3.6-plus", "qwen-vl-max", "qwen-audio-turbo"
    """
    if role not in MODEL_ROLES:
        raise ConfigError(
            f"未知模型角色 '{role}'. 可用角色: {list(MODEL_ROLES.keys())}",
            code="UNKNOWN_MODEL_ROLE",
        )
    store = _get_store()

    def _update(d: dict) -> None:
        if "model_map" not in d:
            d["model_map"] = {}
        d["model_map"][role] = model

    store.update(_update)
    log.info("模型角色 '%s' 已设为 %s", role, model)


# ── 默认配置 ──

DEFAULT_CONFIG: dict = {
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "",
    "model": "qwen3.6-plus",
    "model_map": {
        "director": "qwen3.6-plus",
        "vision": "qwen3.6-plus",
        "audio": "qwen3-omni-flash",
    },
    "settings": {
        "theme": "dark",
        "auto_save": True,
        "auto_update": True,
        "open_last_project": False,
        "output_dir": str(OUTPUT_DIR),
    },
}

_store: JsonStore | None = None


def _get_store() -> JsonStore:
    global _store
    if _store is None:
        _store = JsonStore(CONFIG_FILE, DEFAULT_CONFIG.copy())
        # 确保 config.json 存在
        if not CONFIG_FILE.exists():
            _store.write(DEFAULT_CONFIG.copy())
            log.info("创建默认配置文件: %s", CONFIG_FILE)
    return _store


# ── 公开 API ──

def get_api_key() -> str:
    """获取 API key.

    优先级:环境变量 DASHSCOPE_API_KEY > config.json > 抛异常.
    绝不复用硬编码 fallback.
    """
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if key:
        return key

    store = _get_store()
    config = store.read()
    key = config.get("api_key", "").strip()
    if key:
        return key

    raise ConfigError(
        "API Key 未配置.设置环境变量 DASHSCOPE_API_KEY 或在设置页面填入.",
        code="NO_API_KEY",
    )


def get_base_url() -> str:
    store = _get_store()
    return store.read().get("base_url", DEFAULT_CONFIG["base_url"])


def get_model() -> str:
    store = _get_store()
    return store.read().get("model", DEFAULT_CONFIG["model"])


def get_backend_url() -> str:
    """获取 ClipMind 后端地址"""
    store = _get_store()
    return store.read().get("backend_url", "")


def get_settings() -> dict:
    store = _get_store()
    return store.read().get("settings", DEFAULT_CONFIG["settings"])


def set_api_key(key: str) -> None:
    """设置 API key 并持久化."""
    store = _get_store()
    store.update(lambda d: d.update({"api_key": key}))
    log.info("API key 已更新")


def configure(base_url: str = "", api_key: str = "", model: str = "",
              backend_url: str = "") -> None:
    """批量设置配置.空字符串 = 不修改该项."""
    store = _get_store()

    def _update(d: dict) -> None:
        if base_url:
            d["base_url"] = base_url
        if api_key:
            d["api_key"] = api_key
        if model:
            d["model"] = model
        if backend_url:
            d["backend_url"] = backend_url

    store.update(_update)
    log.info("配置已更新 (base_url=%s, model=%s, api_key=%s, backend_url=%s)",
             base_url or "(不变)", model or "(不变)",
             "***" if api_key else "(不变)",
             backend_url or "(不变)")


def save_settings(settings: dict) -> None:
    """保存用户设置并持久化."""
    store = _get_store()
    store.update(lambda d: d.update({"settings": {**d.get("settings", {}), **settings}}))
    log.info("用户设置已保存")
