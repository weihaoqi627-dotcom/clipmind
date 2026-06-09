"""
ClipMind 结构化日志系统.

替换所有 print() 调用.每条日志自动带时间戳,级别,模块名.

用法:
    from director.logging_config import get_logger
    log = get_logger(__name__)
    log.info("管线启动,素材数=%d", len(videos))
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_logger_cache: dict[str, logging.Logger] = {}
_setup_done = False


def get_logger(name: str) -> logging.Logger:
    """获取 logger.短名称自动加 clipmind. 前缀."""
    if not name.startswith("clipmind."):
        name = f"clipmind.{name}"
    if name in _logger_cache:
        return _logger_cache[name]
    logger = logging.getLogger(name)
    _logger_cache[name] = logger
    return logger


def setup_logging(
    log_dir: Path | str | None = None,
    level: int = logging.INFO,
    force: bool = False,
) -> None:
    """配置日志系统.幂等,重复调用不会加重复 handler.

    Args:
        log_dir: 日志文件目录.None = 只输出到 stderr(开发模式).
        level: 日志级别.
        force: 强制重新配置(测试用).
    """
    global _setup_done

    root = logging.getLogger("clipmind")
    if _setup_done and not force:
        return

    root.setLevel(level)

    # 生产模式:日志写入失败不抛异常(避免因为日志问题崩掉主流程)
    logging.raiseExceptions = os.environ.get("CLIPMIND_DEBUG") == "1"

    # 移除已有的 handler(force 模式)
    if force:
        root.handlers.clear()

    if root.handlers:
        return

    # ── stderr handler ──
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root.addHandler(console)

    # ── 文件 handler(带轮转)──
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "clipmind.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        root.addHandler(file_handler)

    _setup_done = True
    root.info("日志系统初始化完成 (level=%s, file=%s)",
              logging.getLevelName(level),
              log_dir / "clipmind.log" if log_dir else "console-only")
