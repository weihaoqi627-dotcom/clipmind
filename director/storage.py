"""
线程安全的 JSON 文件存储 — 原子写 + 损坏恢复 + 断电保护.

设计目标:
- 多线程读写不丢数据(threading.Lock)
- 写操作原子化(temp -> flush -> fsync -> os.replace -> 父目录 fsync)
- 自动备份 + 损坏恢复(恢复时不覆写备份,保留取证快照)
- 取代全项目的裸 json.load/json.dump

参考:Python logging HOWTO (fsync 序列),atomicwrites,safeatomic
"""

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from .exceptions import StorageError
from .logging_config import get_logger

log = get_logger("storage")


def _safe_fsync(fd: int) -> None:
    """跨平台安全 fsync."""
    try:
        os.fsync(fd)
    except OSError:
        pass  # 某些文件系统(如某些 NAS)不支持 fsync


def _safe_fsync_path(path: Path) -> None:
    """对目录做 fsync(确保目录项写入磁盘)."""
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


class JsonStore:
    """线程安全的 JSON 文件存储.

    特性:
    - 线程级锁(threading.Lock)
    - 断电保护(flush + fsync + 父目录 fsync)
    - 原子写(temp -> os.replace)
    - 写前自动备份(.bak),恢复时不移除备份
    - 损坏恢复(主文件损坏时回退到 .bak)
    """

    def __init__(self, path: Path, default: Any = None):
        self._path = Path(path)
        self._default = default if default is not None else {}
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    # ── 读 ──

    def read(self) -> Any:
        """读取 JSON 数据.主文件损坏时自动尝试 .bak 恢复."""
        with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> Any:
        """内部读,调用方必须持有锁."""
        # 1. 尝试主文件
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                log.warning("JSON 文件损坏: %s (%s),尝试恢复备份", self._path, e)
            except OSError as e:
                log.error("读取 JSON 文件失败: %s (%s)", self._path, e)
                raise StorageError(
                    f"无法读取 JSON 文件: {self._path}",
                    path=str(self._path),
                    detail={"os_error": str(e)},
                ) from e

        # 2. 尝试备份
        bak_path = self._path.with_suffix(self._path.suffix + ".bak")
        if bak_path.exists():
            try:
                with open(bak_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                log.info("从备份恢复: %s -> %s", bak_path, self._path)
                # 恢复主文件(不创建新备份——恢复不是新版本,保留 .bak 作为取证快照)
                self._write_atomic(data, backup=False)
                return data
            except Exception as e:
                log.error("备份恢复也失败: %s (%s),使用默认值", bak_path, e)

        # 3. 默认值(不写入磁盘——保留损坏文件供取证)
        log.info("JSON 文件不存在,返回默认值: %s", self._path)
        return self._default

    # ── 写 ──

    def write(self, data: Any) -> None:
        """原子写入 JSON 数据."""
        with self._lock:
            self._write_atomic(data)

    def _write_atomic(self, data: Any, backup: bool = True) -> None:
        """原子写核心.调用方必须持有锁.

        序列: temp 写 -> flush -> fsync -> os.replace -> 父目录 fsync

        Args:
            data: 要写入的数据
            backup: True = 写前创建 .bak(正常写入).
                     False = 恢复模式,不覆盖 .bak.
        """
        # 备份现有文件(仅正常写入模式)
        if backup and self._path.exists():
            bak_path = self._path.with_suffix(self._path.suffix + ".bak")
            try:
                shutil.copy2(self._path, bak_path)
            except OSError as e:
                log.warning("备份失败 (非致命): %s (%s)", self._path, e)

        # 确保目录存在
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # 写临时文件 -> flush -> fsync -> 原子替换 -> 父目录 fsync
        tmp_fd = -1
        tmp_path = ""
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                suffix=".json",
                prefix=".tmp_",
                dir=str(self._path.parent),
            )
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                _safe_fsync(f.fileno())         # 数据落盘
            os.replace(tmp_path, self._path)     # 原子替换
            _safe_fsync_path(self._path)         # 目录项落盘
        except Exception:
            # 清理临时文件
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise StorageError(
                f"写入 JSON 文件失败: {self._path}",
                path=str(self._path),
            )

    # ── 读-改-写 ──

    def update(self, updater: Callable[[Any], Any]) -> Any:
        """读-改-写,全程持锁.

        updater 接收当前数据,返回任意值(通常修改数据本身).
        """
        with self._lock:
            data = self._read_unlocked()
            result = updater(data)
            self._write_atomic(data)
            return result


# ── 全局单例缓存 ──

_stores: dict[str, JsonStore] = {}
_stores_lock = threading.Lock()


def get_store(path: Path | str, default: Any = None) -> JsonStore:
    """获取或创建共享的 JsonStore 实例.

    同一路径在整个进程内共享同一个 store + 同一个锁.
    """
    key = str(Path(path).resolve())
    with _stores_lock:
        if key not in _stores:
            _stores[key] = JsonStore(Path(path), default)
        return _stores[key]
