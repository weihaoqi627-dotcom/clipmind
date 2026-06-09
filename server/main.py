"""
ClipMind RPC Server(多项目并行版)
======================================
stdio 双向 JSON-RPC 服务.Electron 启动 Python 子进程后,
通过 stdin/stdout 管道通信.

协议:
  请求:   {"id": 1, "method": "name", "params": {..., "project_id": "xxx"}}\n
  响应:   {"id": 1, "result": ...}\n
  事件:   {"event": "type", "project_id": "xxx", ...data...}\n

多项目架构:
  RpcServer
    └─ runners: Dict[str, DirectorRunner]
        ├─ "proj_001" -> Runner(独立 agent loop)
        ├─ "proj_002" -> Runner(独立 agent loop)
        └─ ...

每个项目 = 独立的 DirectorRunner = 独立的 AI agent.
工具相同,各干各的,互不干扰.
"""
import sys
import json
import os
import re
import hashlib
import glob
import threading
import traceback
import time

# ── 项目根 ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# ── 初始化日志(尽早,在所有 import 之前) ──
from director.logging_config import setup_logging, get_logger
from director.config import LOGS_DIR
setup_logging(log_dir=LOGS_DIR, level=os.environ.get("CLIPMIND_LOG_LEVEL", "INFO"))
log = get_logger("server.main")

# ── 存储层 ──
from director.storage import JsonStore, get_store
from director import config as clipmind_config
from director.exceptions import ConfigError, StorageError

# ── 数据目录（优先级: CLIPMIND_DATA_HOME/data > CLIPMIND_DATA_DIR > PROJECT_ROOT/data）─
_DATA_HOME = os.environ.get("CLIPMIND_DATA_HOME", "").strip()
if _DATA_HOME:
    _DATA_DIR = os.path.join(_DATA_HOME, "data")
else:
    _DATA_DIR = os.environ.get("CLIPMIND_DATA_DIR", os.path.join(_PROJECT_ROOT, "data"))
_PROJECTS_FILE = os.path.join(_DATA_DIR, "projects.json")
_CHATS_DIR = os.path.join(_DATA_DIR, "projects")
_TRASH_DIR = os.path.join(_DATA_DIR, "trash")

os.makedirs(_CHATS_DIR, exist_ok=True)
os.makedirs(_TRASH_DIR, exist_ok=True)

# 30 天回收站保留期(秒)
_TRASH_RETENTION = 30 * 24 * 3600

# ── 全局 JsonStore 实例(线程安全) ──
_projects_store = get_store(_PROJECTS_FILE, [])
_config_store = get_store(clipmind_config.CONFIG_FILE, clipmind_config.DEFAULT_CONFIG.copy())

# ── 加载 .env ──
_ENV_LOADED = False
try:
    from dotenv import load_dotenv
    _env_candidates = [
        os.environ.get("CLIPMIND_ENV_PATH", ""),
        os.path.join(_PROJECT_ROOT, ".env"),
    ]
    for _p in _env_candidates:
        if _p and os.path.exists(_p):
            load_dotenv(_p)
            key_set = bool(os.environ.get("DASHSCOPE_API_KEY", ""))
            log.info(".env 已加载 (%s) (DASHSCOPE_API_KEY=%s)", _p, "***" if key_set else "未设置")
            _ENV_LOADED = True
            break
    if not _ENV_LOADED:
        log.info(".env 未找到,使用 config.json 或环境变量")
except ImportError:
    log.info("python-dotenv 未安装,跳过 .env 加载")

# ── 如果没有 DASHSCOPE_API_KEY，解内嵌加密密钥 ──
if not os.environ.get("DASHSCOPE_API_KEY", ""):
    try:
        import base64
        _EMBEDDED_XOR_KEY = b"ClipMind_S3cret!"
        _EMBEDDED_API_KEY = "MAdEQCwMWlVsNwsHFFwRFXENXkh6DQ8HPmJWAUVSEBB0XQ0="
        _data = base64.b64decode(_EMBEDDED_API_KEY)
        _dec = bytes(_data[i] ^ _EMBEDDED_XOR_KEY[i % len(_EMBEDDED_XOR_KEY)] for i in range(len(_data)))
        os.environ["DASHSCOPE_API_KEY"] = _dec.decode()
        log.info("内嵌密钥已解密并注入环境变量")
    except Exception:
        log.warning("内嵌密钥解密失败，DASHSCOPE_API_KEY 未设置")

from server.director_runner import DirectorRunner


# ─── 输出管道(解耦业务线程与 stdout,防止管道反压阻塞)────────

# 分析线程调用 write_json() 时,如果前端不读 stdout,
# sys.stdout.write() 会阻塞 -> 分析线程卡死.
# 用后台写入线程 + 有界队列解耦:业务线程 put_nowait() 不阻塞.

import queue as _queue_mod

_stdout_queue = _queue_mod.Queue(maxsize=2000)
_writer_running = False


def _stdout_writer_thread():
    """唯一允许阻塞在 stdout 上的线程"""
    while True:
        try:
            line = _stdout_queue.get(timeout=1.0)
            if line is None:
                break
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except _queue_mod.Empty:
            continue
        except (BrokenPipeError, OSError, ValueError):
            break


def start_stdout_writer():
    global _writer_running
    if _writer_running:
        return
    _writer_running = True
    t = threading.Thread(target=_stdout_writer_thread, daemon=True)
    t.start()


def write_json(obj: dict):
    """非阻塞写入.管道满时记录警告并丢弃消息,不阻塞调用者."""
    if not _writer_running:
        start_stdout_writer()
    line = json.dumps(obj, ensure_ascii=False)
    try:
        _stdout_queue.put_nowait(line)
    except _queue_mod.Full:
        log.warning("stdout 队列满 (%d),丢弃事件: %s",
                     _stdout_queue.maxsize,
                     obj.get("event", obj.get("id", "?"))[:60])


# ─── 聊天文件路径 ───────────────────────────────────────────

def _chat_file(project_id: str) -> str:
    return os.path.join(_CHATS_DIR, f"{project_id}.json")

# 聊天存储(每个项目独立 store,不缓存以避免内存膨胀)
def _get_chat_store(project_id: str) -> JsonStore:
    return JsonStore(os.path.join(_CHATS_DIR, f"{project_id}.json"), [])


# ─── RPC Server(多项目版)───────────────────────────────────

class RpcServer:
    """stdio JSON-RPC 服务端 — 支持多项目并行"""

    def __init__(self):
        self.runners: dict[str, DirectorRunner] = {}
        self._running = False

    @staticmethod
    def _get_effective_config() -> dict:
        """获取当前有效配置(config.json + 环境变量合并)"""
        saved = _config_store.read()
        merged = {**clipmind_config.DEFAULT_CONFIG, **saved}
        return merged

    def _get_runner(self, project_id: str) -> DirectorRunner:
        """获取或创建项目的 Runner"""
        if not project_id:
            raise ValueError("project_id 不能为空")
        if project_id not in self.runners:
            self.runners[project_id] = DirectorRunner(
                event_callback=lambda e, d: self._on_event(project_id, e, d)
            )
            # 应用全局配置
            runner = self.runners[project_id]
            try:
                base_url = clipmind_config.get_base_url()
                api_key = clipmind_config.get_api_key()
                model = clipmind_config.get_model()
                backend_url = clipmind_config.get_backend_url()
                runner.configure(base_url=base_url, api_key=api_key, model=model, backend_url=backend_url)
            except ConfigError:
                # API key 未配置 — runner 仍然可用,只是 LLM 调用会失败
                log.warning("项目 %s: API key 未配置,LLM 调用将失败", project_id)
                # 使用已获取的值(成功了的),失败的用默认值
                try:
                    b = clipmind_config.get_base_url()
                except ConfigError:
                    b = "https://dashscope.aliyuncs.com/compatible-mode/v1"
                try:
                    m = clipmind_config.get_model()
                except ConfigError:
                    m = "qwen3.6-plus"
                runner.configure(base_url=b, api_key="", model=m, backend_url=clipmind_config.get_backend_url())
        return self.runners[project_id]

    def _on_event(self, project_id: str, event_type: str, data: dict):
        """Runner 事件回调 -> 包 project_id -> 推送给 Electron"""
        payload = {"event": event_type, "project_id": project_id}
        payload.update(data)
        write_json(payload)

    def start(self):
        """主循环"""
        self._running = True
        # 启动时清理过期回收站项目
        try:
            purged = self._purge_expired_trash()
            if purged.get("purged", 0) > 0:
                log.info("启动清理: %d 个过期回收站项目已清除", purged["purged"])
        except Exception as e:
            log.warning("启动时清理回收站失败: %s", e)
        log.info("RPC 服务启动")
        try:
            while self._running:
                line = sys.stdin.readline()
                if not line:
                    log.info("stdin 关闭,退出主循环")
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("无法解析的 JSON: %.100s...", line[:100])
                    continue

                rid = req.get("id")
                method = req.get("method", "")
                params = req.get("params", {})

                try:
                    result = self._handle(method, params, rid)
                    if rid is not None and result != "__handled__":
                        write_json({"id": rid, "result": result})
                except (ValueError, TypeError) as e:
                    # 已知的输入错误 -> 返回给前端
                    log.warning("请求 %s 参数错误: %s", method, e)
                    if rid is not None:
                        write_json({
                            "id": rid,
                            "error": {"message": str(e)},
                        })
                except Exception as e:
                    # 意外错误 -> 记录完整栈 + 返回给前端
                    log.exception("处理请求 %s 时发生未预期错误", method)
                    if rid is not None:
                        write_json({
                            "id": rid,
                            "error": {"message": f"{type(e).__name__}: {str(e)}"},
                        })
        except KeyboardInterrupt:
            log.info("收到中断信号")
        except Exception:
            log.exception("RPC 主循环崩溃")
        finally:
            self._running = False
            log.info("RPC 服务停止")

    def _handle(self, method: str, params: dict, rid: int = None):
        """分发请求"""
        handlers = {
            "create_project": self._create_project,
            "delete_project": self._delete_project,
            "list_projects": self._list_projects,
            "configure": self._configure,
            "start_project": self._start_project,
            "send_message": self._send_message,
            "respond_ask": self._respond_ask,
            "respond_preview_clip": self._respond_preview_clip,
            "get_waveform": self._get_waveform,
            "cancel": self._cancel,
            "confirm_plan": self._confirm_plan,
            "chat": self._chat,
            "get_draft_info": self._get_draft_info,
            "list_drafts": self._list_drafts,
            "reorder_clips": self._reorder_clips,
            "delete_draft": self._delete_draft,
            "save_chat_messages": self._save_chat_messages,
            "load_chat_messages": self._load_chat_messages,
            "update_project": self._update_project,
            "export_draft": self._export_draft,
            "save_feedback": self._save_feedback,
            "get_settings": self._get_settings,
            "save_settings": self._save_settings,
            "export_project_report": self._export_project_report,
            "get_api_config": self._get_api_config,
            "start_pipeline": self._start_pipeline,
            "restore_project": self._restore_project,
            "permanently_delete_project": self._permanently_delete_project,
            "list_trash": self._list_trash,
            "rescan_projects": self._rescan_projects,
            "suggest_project_name": self._suggest_project_name,
        }
        # 注册 shutdown —— Electron 关闭时调用,优雅退出
        # 注意: _handle 以 fn(**params) 调用,params={} 时不能要求参数
        handlers["shutdown"] = lambda **_: sys.stdin.close()
        fn = handlers.get(method)
        if not fn:
            raise ValueError(f"未知方法: {method}")
        # 将 rid 注入 params — 让 _start_project / _send_message 等
        # 能通过 rid 调用 _ok() 提前返回响应("__handled__"模式)
        _RID_AWARE = frozenset({
            "start_project", "start_pipeline", "send_message", "cancel",
        })
        if rid is not None and method in _RID_AWARE:
            params = {**params, "rid": rid}
        return fn(**params)

    # ── 项目管理 ──

    def _generate_date_name(self):
        """生成日期基准的项目名,如 '5.31','5.31 (1)','5.31 (2)'"""
        month = str(int(time.strftime("%m")))
        day = str(int(time.strftime("%d")))
        base = f"{month}.{day}"
        projects = _projects_store.read()
        existing = {p.get("name", "") for p in projects}
        if base not in existing:
            return base
        i = 1
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"

    def _create_project(self, name: str = ""):
        """创建新项目,返回 project_id"""
        pid = f"proj_{int(time.time() * 1000)}"
        # 预创建 runner(应用全局配置)
        self._get_runner(pid)
        # 持久化
        project_name = name or self._generate_date_name()
        projects = _projects_store.read()
        projects.insert(0, {
            "project_id": pid,
            "name": project_name,
            "name_locked": bool(name),  # 用户传了名字就锁定
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "draft_id": "",
            "materials": [],
        })
        _projects_store.write(projects)
        write_json({"event": "project_created", "project_id": pid, "name": project_name})
        return {"project_id": pid, "name": project_name}

    def _delete_project(self, project_id: str = ""):
        """软删除项目:移入回收站"""
        if not project_id:
            raise ValueError("project_id 不能为空")
        if project_id in self.runners:
            self.runners[project_id].cancel()
            del self.runners[project_id]
        projects = _projects_store.read()
        now = time.time()
        for p in projects:
            if p.get("project_id") == project_id:
                p["deleted_at"] = now  # 标记删除时间
                p["deleted_name"] = p.get("name", f"项目 {project_id[-6:]}")
                break
        _projects_store.write(projects)
        write_json({"event": "project_deleted", "project_id": project_id})
        return "ok"

    def _restore_project(self, project_id: str = ""):
        """从回收站恢复项目"""
        if not project_id:
            raise ValueError("project_id 不能为空")
        projects = _projects_store.read()
        for p in projects:
            if p.get("project_id") == project_id and p.get("deleted_at"):
                del p["deleted_at"]
                p.pop("deleted_name", None)
                _projects_store.write(projects)
                write_json({"event": "project_restored", "project_id": project_id})
                return {"ok": True}
        return {"ok": False, "error": "项目不存在或不在回收站中"}

    def _permanently_delete_project(self, project_id: str = ""):
        """永久删除项目(从硬盘彻底清除)"""
        if not project_id:
            raise ValueError("project_id 不能为空")
        # 删聊天文件
        cf = _chat_file(project_id)
        for f in [cf, cf + ".bak"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError as e:
                    log.warning("清理项目 %s 文件失败: %s", project_id, e)
        # 删草稿目录
        draft_dir = os.path.join(_PROJECT_ROOT, "drafts")
        for d in os.listdir(draft_dir) if os.path.isdir(draft_dir) else []:
            d_path = os.path.join(draft_dir, d)
            if os.path.isdir(d_path):
                state_file = os.path.join(d_path, "pipeline_state.json")
                if os.path.exists(state_file):
                    try:
                        import json as _json
                        with open(state_file, "r", encoding="utf-8") as _sf:
                            st = _json.load(_sf)
                        if st.get("draft_id", "").startswith(project_id):
                            import shutil
                            shutil.rmtree(d_path, ignore_errors=True)
                    except Exception:
                        pass
        # 从索引中移除
        projects = _projects_store.read()
        projects = [p for p in projects if p.get("project_id") != project_id]
        _projects_store.write(projects)
        write_json({"event": "project_permanently_deleted", "project_id": project_id})
        return "ok"

    def _list_trash(self):
        """列出回收站中的项目"""
        projects = _projects_store.read()
        trash = [
            {
                "project_id": p.get("project_id", "unknown"),
                "name": p.get("deleted_name", p.get("name", "未命名项目")),
                "created_at": p.get("created_at", ""),
                "deleted_at": p.get("deleted_at", 0),
                "draft_id": p.get("draft_id", ""),
                "materials_count": len(p.get("materials", [])),
            }
            for p in projects if p.get("deleted_at")
        ]
        trash.sort(key=lambda x: x.get("deleted_at", 0), reverse=True)
        return trash

    def _purge_expired_trash(self):
        """清理超过保留期的回收站项目(启动时自动调用)"""
        now = time.time()
        projects = _projects_store.read()
        expired = [p for p in projects if p.get("deleted_at") and (now - p["deleted_at"]) > _TRASH_RETENTION]
        if not expired:
            return {"purged": 0}
        pids = [p["project_id"] for p in expired]
        projects = [p for p in projects if p["project_id"] not in pids]
        _projects_store.write(projects)
        # 清理聊天文件
        for pid in pids:
            cf = _chat_file(pid)
            for f in [cf, cf + ".bak"]:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
        log.info("清理了 %d 个过期回收站项目: %s", len(pids), ", ".join(pids))
        return {"purged": len(pids)}

    def _list_projects(self):
        """列出所有项目(持久化项目 + 运行时状态)"""
        try:
            saved = _projects_store.read()
        except StorageError as e:
            log.error("读取项目列表失败: %s", e)
            saved = []

        # 收集索引中的 project_id,用于判断孤儿
        indexed_ids = {p.get("project_id", "") for p in saved}

        # 扫描 _CHATS_DIR 下的孤立项目文件,自动恢复
        orphan_recovered = 0
        if os.path.isdir(_CHATS_DIR):
            for fname in os.listdir(_CHATS_DIR):
                if not fname.endswith(".json") or fname.endswith(".bak"):
                    continue
                pid = fname[:-5]  # 去掉 .json 后缀
                if pid in indexed_ids:
                    continue
                # 这是个孤立项目文件——有聊天数据但索引丢了
                try:
                    chat_path = os.path.join(_CHATS_DIR, fname)
                    # 确认文件非空且有实际聊天内容(不只是空数组)
                    chat_data = _get_chat_store(pid).read()
                    if not chat_data or not isinstance(chat_data, list) or len(chat_data) == 0:
                        continue  # 空文件不恢复
                    # 从文件名解析时间戳(proj_1234567890123 -> 1234567890123)
                    ts_match = re.match(r"proj_(\d+)", pid)
                    created_at = ""
                    if ts_match:
                        ts_ms = int(ts_match.group(1))
                        created_at = time.strftime(
                            "%Y-%m-%d %H:%M:%S",
                            time.localtime(ts_ms / 1000),
                        )
                    # 自动注册到索引
                    entry = {
                        "project_id": pid,
                        "name": f"项目 {pid[-6:]}",
                        "created_at": created_at,
                        "draft_id": "",
                        "materials": [],
                    }
                    saved.insert(0, entry)
                    indexed_ids.add(pid)
                    orphan_recovered += 1
                    log.info("自动恢复孤立项目: %s (%d 条聊天记录)", pid, len(chat_data))
                except Exception as e:
                    log.warning("扫描孤立项目 %s 失败: %s", pid, e)

        if orphan_recovered > 0:
            # 持久化恢复结果
            try:
                _projects_store.write(saved)
            except Exception as e:
                log.error("持久化恢复的项目列表失败: %s", e)

        result = []
        for p in saved:
            # 跳过回收站中的项目
            if p.get("deleted_at"):
                continue
            pid = p.get("project_id", "")
            if not pid:
                continue
            runner = self.runners.get(pid)
            entry = {
                "project_id": pid,
                "name": p.get("name", f"项目 {pid[-6:]}"),
                "name_locked": p.get("name_locked", False),
                "created_at": p.get("created_at", ""),
                "draft_id": p.get("draft_id", ""),
                "materials": p.get("materials", []),
                "running": runner.is_running if runner else False,
                "turns_used": getattr(runner.last_state, "turns_used", 0) if runner and runner.last_state else 0,
            }
            result.append(entry)

        return result

    def _rescan_projects(self):
        """手动触发扫描孤立项目文件并恢复"""
        return self._list_projects()

    # ── 全局方法 ──

    def _configure(self, base_url: str = "", api_key: str = "", model: str = "",
                    backend_url: str = ""):
        """设置全局配置并应用到所有 runner"""
        clipmind_config.configure(base_url=base_url, api_key=api_key, model=model, backend_url=backend_url)
        # 应用到已有 runner
        effective_base_url = base_url or clipmind_config.get_base_url()
        effective_key = api_key or (clipmind_config.get_api_key() if _has_api_key() else "")
        effective_model = model or clipmind_config.get_model()
        for runner in self.runners.values():
            runner.configure(
                base_url=effective_base_url,
                api_key=effective_key,
                model=effective_model,
                backend_url=backend_url,
            )
        return "ok"

    def _get_settings(self):
        """获取用户设置"""
        try:
            return clipmind_config.get_settings()
        except Exception:
            return clipmind_config.DEFAULT_CONFIG.get("settings", {})

    def _save_settings(self, settings: dict = None):
        """保存用户设置"""
        if not settings:
            raise ValueError("settings 不能为空")
        clipmind_config.save_settings(settings)
        return "ok"

    def _get_api_config(self):
        """获取 API 配置(key 脱敏)"""
        try:
            key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
            env_key = bool(key)
            if not key:
                key = clipmind_config.get_api_key()
            # 脱敏:只显示前4位 + 后4位
            masked = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
            return {
                "base_url": clipmind_config.get_base_url(),
                "model": clipmind_config.get_model(),
                "api_key_masked": masked,
                "env_key": env_key,  # true = key 来自 .env,不可在 UI 修改
            }
        except ConfigError:
            return {
                "base_url": clipmind_config.get_base_url(),
                "model": clipmind_config.get_model(),
                "api_key_masked": "",
                "env_key": False,
            }

    def _ok(self, rid: int):
        """立即写入响应"""
        write_json({"id": rid, "result": "ok"})

    # ── 项目级方法(都需要 project_id)──

    def _start_project(self, paths: list = None, task: str = "",
                       project_id: str = "", rid: int = None):
        if rid is not None:
            self._ok(rid)
        runner = self._get_runner(project_id)
        # 从 projects.json 读取项目名,传给 runner 用于文件路径对齐
        project_name = ""
        for p in _projects_store.read():
            if p.get("project_id") == project_id:
                project_name = p.get("name", "")
                break
        runner.start_project(paths or [], task, project_name=project_name)
        return "__handled__"

    def _start_pipeline(self, project_id: str = "", rid: int = None):
        """用户点"开始" -> Director 启动 Pipeline 逐阶段执行"""
        if rid is not None:
            self._ok(rid)
        runner = self._get_runner(project_id)
        runner.start_pipeline()
        return "__handled__"

    def _send_message(self, text: str = "", project_id: str = "",
                      rid: int = None):
        if not text:
            raise ValueError("text 不能为空")
        if rid is not None:
            self._ok(rid)
        runner = self._get_runner(project_id)
        runner.send_message(text)
        return "__handled__"

    def _respond_ask(self, text: str = "", project_id: str = ""):
        runner = self._get_runner(project_id)
        runner.respond_ask(text)
        return "ok"

    def _respond_preview_clip(self, data: str = "", project_id: str = ""):
        runner = self._get_runner(project_id)
        runner.respond_preview_clip(data)
        return "ok"

    def _cancel(self, project_id: str = "", rid: int = None):
        if rid is not None:
            self._ok(rid)
        runner = self._get_runner(project_id)
        runner.cancel()
        return "__handled__"

    def _confirm_plan(self, project_id: str = ""):
        runner = self._get_runner(project_id)
        runner.confirm_plan()
        return "ok"

    def _get_waveform(self, audio_path: str = "", num_bars: int = 200):
        """获取音频波形数据(不需要 project_id,跟项目无关)"""
        if not audio_path or not os.path.exists(audio_path):
            raise ValueError(f"音频文件不存在: {audio_path}")
        from director.tools.audio import _get_waveform_raw
        return _get_waveform_raw(audio_path, num_bars)

    def _chat(self, text: str = "", project_id: str = ""):
        if not text:
            raise ValueError("text 不能为空")
        runner = self._get_runner(project_id)
        runner.chat(text)
        return "ok"

    # ── 草稿查询(无状态,不需要 project_id)──

    def _get_draft_info(self, draft_id: str = ""):
        """获取草稿完整信息,包括时间线,素材,输出文件路径"""
        if not draft_id:
            raise ValueError("draft_id 不能为空")

        from director.draft import Draft
        draft = Draft(draft_id)
        data = draft.load()
        if data is None:
            raise ValueError(f"草稿 {draft_id} 不存在")

        versions = draft.list_versions()
        summary = draft.get_summary()
        tl = data.get("timeline", {})
        audio = data.get("audio", {})
        render_settings = data.get("render_settings", {})

        # 推断输出文件路径
        output_path = _find_draft_output(data)

        return {
            "draft_id": draft_id,
            "name": data.get("name", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "current_version": data.get("version", 0),
            "version_label": data.get("version_label", ""),
            "version_count": len(versions),
            "versions": [{
                "version": v.get("version", 0),
                "label": v.get("label", ""),
                "updated_at": v.get("updated_at", ""),
                "file": v.get("file", ""),
            } for v in versions],
            "source_videos": data.get("source_videos", []),
            "segments": tl.get("main_track", {}).get("segments", []),
            "transitions": tl.get("main_track", {}).get("transitions", []),
            "overlays": tl.get("overlay_track", []),
            "graphics": tl.get("graphic_track", []),
            "flower_texts": tl.get("flower_texts", []),
            "subtitles": tl.get("subtitles", []),
            "transcript": data.get("transcript"),
            "bgm": audio.get("bgm"),
            "bgm_ducking": audio.get("bgm_ducking", True),
            "vocal_track": audio.get("vocal_track"),
            "sfx": audio.get("sfx", []),
            "output_format": render_settings.get("output_format", "mp4"),
            "quality": render_settings.get("quality", "high"),
            "has_output": bool(output_path),
            "output_path": output_path,
            "output_size_mb": round(os.path.getsize(output_path) / (1024*1024), 1) if output_path else 0,
            "summary": summary,
        }

    def _list_drafts(self):
        """列出所有草稿(摘要)"""
        results = []
        drafts_dir = os.path.join(_PROJECT_ROOT, "drafts")
        if not os.path.isdir(drafts_dir):
            return results

        for dname in sorted(os.listdir(drafts_dir), reverse=True):
            dp = os.path.join(drafts_dir, dname)
            if not os.path.isdir(dp):
                continue
            # 找最新版本
            ver_files = sorted(glob.glob(os.path.join(dp, "v*.json")))
            if not ver_files:
                continue
            try:
                with open(ver_files[-1], "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("跳过损坏的草稿 %s: %s", dname, e)
                continue

            output_path = _find_draft_output(data)

            results.append({
                "draft_id": dname,
                "name": data.get("name", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "version": data.get("version", 0),
                "version_label": data.get("version_label", ""),
                "version_count": len(ver_files),
                "source_videos": data.get("source_videos", []),
                "segment_count": len(data.get("timeline", {}).get("main_track", {}).get("segments", [])),
                "has_bgm": data.get("audio", {}).get("bgm") is not None,
                "has_output": bool(output_path),
                "output_path": output_path,
                "output_size_mb": round(os.path.getsize(output_path) / (1024*1024), 1) if output_path else 0,
            })

        return results

    def _reorder_clips(self, draft_id: str, clip_ids: list[int]):
        """重新排序主轨道片段"""
        from director.draft import Draft

        if not draft_id:
            return {"ok": False, "error": "draft_id 不能为空"}

        d = Draft(draft_id)
        if not d.load():
            return {"ok": False, "error": f"草稿 {draft_id} 不存在"}

        ok = d.reorder_segments(clip_ids)
        if not ok:
            return {"ok": False, "error": "重排失败,片段 ID 与草稿不匹配"}

        d.save("重新排序片段")
        return {"ok": True, "draft_id": draft_id, "new_order": clip_ids}

    # ── 草稿管理 ──

    def _delete_draft(self, draft_id: str = ""):
        """删除草稿(物理删除整个草稿目录 + 清理相关输出文件)"""
        import shutil
        if not draft_id:
            raise ValueError("draft_id 不能为空")
        draft_dir = os.path.join(_PROJECT_ROOT, "drafts", draft_id)
        deleted = False
        if os.path.isdir(draft_dir):
            # 先清理相关 output 文件
            try:
                from director.draft import Draft
                d = Draft(draft_id)
                data = d.load()
                if data:
                    out = _find_draft_output(data)
                    if out and os.path.exists(out):
                        os.remove(out)
                        log.info("已清理输出文件: %s", out)
            except Exception as e:
                log.warning("清理草稿 %s 输出文件时出错: %s", draft_id, e)
            try:
                shutil.rmtree(draft_dir)
                deleted = True
                log.info("已删除草稿目录: %s", draft_dir)
            except OSError as e:
                log.error("删除草稿目录失败: %s (%s)", draft_dir, e)
                raise
        # 同步清理 projects.json 中引用此草稿的项目
        if deleted:
            projects = _projects_store.read()
            changed = False
            for p in projects:
                if p.get("draft_id") == draft_id:
                    p["draft_id"] = ""
                    changed = True
            if changed:
                _projects_store.write(projects)
        return {"ok": deleted, "draft_id": draft_id}

    # ── 项目报告导出 ──

    def _export_project_report(self, draft_id: str = ""):
        """生成项目完整报告(Markdown)"""
        if not draft_id:
            raise ValueError("draft_id 不能为空")

        from director.draft import Draft
        draft = Draft(draft_id)
        data = draft.load()
        if data is None:
            raise ValueError(f"草稿 {draft_id} 不存在")

        lines = []
        w = lambda s="": lines.append(s)

        name = data.get("name", draft_id)
        tl = data.get("timeline", {})
        audio = data.get("audio", {})
        render = data.get("render_settings", {})
        src = data.get("source_videos", [])
        mt = tl.get("main_track", {})
        segments = mt.get("segments", [])
        transitions = mt.get("transitions", [])
        overlays = tl.get("overlay_track", [])
        graphics = tl.get("graphic_track", [])
        flower_texts = tl.get("flower_texts", [])
        subtitles = tl.get("subtitles", [])
        transcript = data.get("transcript")
        output_path = _find_draft_output(data)

        # ── 标题 ──
        w(f"# {name}")
        w()
        w(f"> 草稿 ID: `{draft_id}`")
        w(f"> 创建时间: {data.get('created_at', '—')}")
        w(f"> 最后更新: {data.get('updated_at', '—')}")
        w(f"> 版本: v{data.get('version', 0)} — {data.get('version_label', '')}")
        w()

        # ── 源素材 ──
        w("## 源素材")
        w()
        if src:
            for i, v in enumerate(src):
                fname = os.path.basename(v)
                size_mb = ""
                if os.path.exists(v):
                    size_mb = f" ({os.path.getsize(v) / (1024*1024):.1f} MB)"
                w(f"- `{fname}`{size_mb}")
            w()
        else:
            w("*(无)*")
            w()

        # ── 时间线参数 ──
        w("## 时间线参数")
        w()
        w(f"| 分辨率 | {tl.get('width', 1920)} x {tl.get('height', 1080)} |")
        w(f"| 帧率 | {tl.get('fps', 30)} fps |")
        w()

        # ── 主轨道片段 ──
        w("## 主轨道片段")
        w()
        total_dur = 0
        if segments:
            for seg in segments:
                sid = seg.get("id", "?")
                dur = seg.get("duration", seg.get("end", 0) - seg.get("start", 0))
                total_dur += dur
                sp = seg.get("source_path", "")
                src_name = os.path.basename(sp) if sp else "—"
                w(f"### 片段 {sid}")
                w()
                w(f"| 属性 | 值 |")
                w(f"|------|------|")
                w(f"| 源文件 | `{src_name}` |")
                w(f"| 起始 | {_fmt_ts(seg.get('start', 0))} |")
                w(f"| 结束 | {_fmt_ts(seg.get('end', 0))} |")
                w(f"| 时长 | {_fmt_ts(dur)} |")
                w(f"| 变速 | {seg.get('speed', 1.0)}x |")
                if seg.get("text"):
                    w(f"| 备注 | {seg['text']} |")
                if seg.get("status"):
                    w(f"| 状态 | {seg['status']} |")
                w()

                # 滤镜 / 特效
                filters = seg.get("filters", {})
                active_filters = {k: v for k, v in (filters or {}).items() if v is not None}
                if active_filters:
                    w("**滤镜与特效:**")
                    w()
                    for fk, fv in active_filters.items():
                        w(f"- **{_fn_label(fk)}**: {_fmt_filter(fk, fv)}")
                    w()

            w(f"> 总时长: {_fmt_ts(total_dur)}  |  片段数: {len(segments)}")
            w()
        else:
            w("*(无)*")
            w()

        # ── 转场 ──
        w("## 转场")
        w()
        if transitions:
            for t in transitions:
                w(f"- 片段 {t.get('from_clip', '?')} -> {t.get('to_clip', '?')}  |  `{t.get('type', '?')}`  |  时长: {_fmt_ts(t.get('duration', 0))}")
            w()
        else:
            w("*(无)*")
            w()

        # ── 花字 ──
        w("## 花字")
        w()
        if flower_texts:
            for ft in flower_texts:
                w(f"- [{_fmt_ts(ft.get('start', 0))} -> {_fmt_ts(ft.get('end', 0))}] **{ft.get('text', '')}**")
                if ft.get("style"):
                    w(f"  - 样式: `{ft['style']}`")
                if ft.get("animation"):
                    w(f"  - 动画: `{ft['animation']}`")
            w()
        else:
            w("*(无)*")
            w()

        # ── 字幕 ──
        w("## 字幕")
        w()
        if subtitles:
            for sub in subtitles:
                w(f"- [{_fmt_ts(sub.get('start', 0))} -> {_fmt_ts(sub.get('end', 0))}] {sub.get('text', '')}")
            w()
            w(f"> 共 {len(subtitles)} 条字幕")
            w()
        else:
            w("*(无)*")
            w()

        # ── 叠层轨道 ──
        w("## 叠层轨道 (Overlay)")
        w()
        if overlays:
            for ov in overlays:
                w(f"- [{_fmt_ts(ov.get('start', 0))} -> {_fmt_ts(ov.get('end', 0))}] `{os.path.basename(ov.get('source_path', '?'))}`")
            w()
        else:
            w("*(无)*")
            w()

        # ── 图片/贴纸轨道 ──
        w("## 图片 / 贴纸轨道")
        w()
        if graphics:
            for g in graphics:
                w(f"- [{_fmt_ts(g.get('start', 0))} -> {_fmt_ts(g.get('end', 0))}] `{os.path.basename(g.get('source_path', '?'))}`")
            w()
        else:
            w("*(无)*")
            w()

        # ── 音频 ──
        w("## 音频")
        w()
        bgm = audio.get("bgm")
        if bgm:
            bgm_src = bgm.get("source", "") if isinstance(bgm, dict) else str(bgm)
            bgm_vol = bgm.get("volume", 0) if isinstance(bgm, dict) else 0
            w(f"| BGM | `{os.path.basename(bgm_src)}` | 音量: {bgm_vol} dB |")
        else:
            w(f"| BGM | *(无)* |")
        w(f"| BGM 闪避 | {'是' if audio.get('bgm_ducking', True) else '否'} |")
        vocal = audio.get("vocal_track")
        if vocal:
            w(f"| 人声轨 | `{os.path.basename(vocal)}` |")
        vo = audio.get("voiceover")
        if vo:
            w(f"| 配音 | `{os.path.basename(vo)}` |")

        sfx_list = audio.get("sfx", [])
        if sfx_list:
            w()
            w("**音效:**")
            w()
            for sfx in sfx_list:
                sfx_src = sfx.get("source", "") if isinstance(sfx, dict) else str(sfx)
                sfx_start = sfx.get("start_time", 0) if isinstance(sfx, dict) else 0
                w(f"- [{_fmt_ts(sfx_start)}] `{os.path.basename(sfx_src)}`")
        w()

        # ── 转录文本 ──
        w("## 转录文本")
        w()
        if transcript:
            ts_segs = transcript.get("segments", []) if isinstance(transcript, dict) else []
            if ts_segs:
                for ts in ts_segs:
                    w(f"- [{_fmt_ts(ts.get('start', 0))} -> {_fmt_ts(ts.get('end', 0))}] {ts.get('text', '')}")
                w()
            else:
                w("*(无转录数据)*")
                w()
        else:
            w("*(无)*")
            w()

        # ── 渲染设置 ──
        w("## 渲染设置")
        w()
        w(f"| 输出格式 | {render.get('output_format', 'mp4')} |")
        w(f"| 质量 | {render.get('quality', 'high')} |")
        if render.get("nvenc"):
            w(f"| 硬件编码 | {render['nvenc']} |")
        if render.get("output_path"):
            w(f"| 输出路径 | `{render['output_path']}` |")
        w()

        # ── 输出文件 ──
        w("## 输出文件")
        w()
        if output_path and os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            w(f"- **{os.path.basename(output_path)}** ({size_mb:.1f} MB)")
            w(f"- 路径: `{output_path}`")
        else:
            w("*(未渲染)*")
        w()

        # ── 页脚 ──
        w("---")
        w(f"*报告由 ClipMind 自动生成 · {time.strftime('%Y-%m-%d %H:%M:%S')}*")

        return {"markdown": "\n".join(lines), "draft_id": draft_id, "name": name}

    # ── 导出 ──

    def _export_draft(self, draft_id: str = "", preset: str = "", project_id: str = ""):
        """手动导出草稿(在后台线程运行,不阻塞 RPC 事件循环)"""
        if not draft_id:
            raise ValueError("draft_id 不能为空")

        def _run():
            try:
                payload = {"event": "export_started", "draft_id": draft_id}
                if project_id:
                    payload["project_id"] = project_id
                write_json(payload)
                log.info("开始导出草稿: %s", draft_id)
                from director.tools.render import render_from_draft
                result = render_from_draft(draft_id=draft_id, preset=preset or "")
                # 查找输出文件
                from director.draft import Draft
                draft = Draft(draft_id)
                data = draft.load()
                output_path = _find_draft_output(data) if data else ""
                payload = {
                    "event": "export_complete",
                    "draft_id": draft_id,
                    "output_path": output_path,
                    "result": str(result),
                }
                if project_id:
                    payload["project_id"] = project_id
                write_json(payload)
                log.info("导出完成: %s -> %s", draft_id, output_path)
            except Exception as e:
                log.exception("导出草稿 %s 失败", draft_id)
                payload = {
                    "event": "export_error",
                    "draft_id": draft_id,
                    "error": f"{type(e).__name__}: {str(e)}",
                }
                if project_id:
                    payload["project_id"] = project_id
                write_json(payload)

        threading.Thread(target=_run, daemon=True).start()
        return "ok"

    # ── 反馈 ──

    def _save_feedback(self, project_id: str = "", draft_id: str = "",
                       rating: int = 0, comment: str = ""):
        """保存用户反馈"""
        _FEEDBACK_DIR = os.path.join(_DATA_DIR, "feedback")
        ts = time.strftime("%Y-%m-%d_%H%M%S")
        filename = os.path.join(_FEEDBACK_DIR,
            f"{draft_id or project_id}_{ts}.json")
        data = {
            "project_id": project_id,
            "draft_id": draft_id,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "user",
            "rating": rating,  # 1-5
            "comment": comment,
        }
        store = JsonStore(filename, {})
        store.write(data)
        log.info("反馈已保存: %s (评分=%d)", draft_id or project_id, rating)
        return {"ok": True}

    # ── 聊天持久化 ──

    def _save_chat_messages(self, project_id: str = "", messages: list = None):
        """持久化聊天记录"""
        if not project_id or messages is None:
            return {"ok": False, "error": "project_id 和 messages 不能为空"}
        store = _get_chat_store(project_id)
        store.write(messages)
        return {"ok": True}

    def _load_chat_messages(self, project_id: str = ""):
        """加载聊天记录"""
        if not project_id:
            raise ValueError("project_id 不能为空")
        store = _get_chat_store(project_id)
        return store.read()

    def _update_project(self, project_id: str = "", name: str = None,
                        draft_id: str = None, materials: list = None):
        """更新项目元数据(素材,草稿 ID 等)"""
        projects = _projects_store.read()
        for p in projects:
            if p.get("project_id") == project_id:
                if name is not None:
                    p["name"] = name
                    p["name_locked"] = True  # 用户传名字 -> 锁定
                if draft_id is not None:
                    p["draft_id"] = draft_id
                if materials is not None:
                    p["materials"] = materials
                _projects_store.write(projects)
                return {"ok": True}
        log.warning("更新项目失败: 项目 %s 不存在", project_id)
        return {"ok": False, "error": "项目不存在"}

    def _suggest_project_name(self, project_id: str = ""):
        """根据第一个素材文件名建议项目名(不锁定,等用户确认或 Director 对话后精炼)"""
        projects = _projects_store.read()
        for p in projects:
            if p.get("project_id") == project_id:
                # 已被锁定 -> 不改
                if p.get("name_locked"):
                    return {"name": p.get("name", "未命名项目"), "locked": True}
                materials = p.get("materials", [])
                if not materials:
                    return {"name": p.get("name", "未命名项目"), "locked": False}
                # 取第一个素材的文件名(去扩展名)
                first = materials[0].get("name", "")
                name_no_ext = re.sub(r"\.\w+$", "", first)
                # 清理常见噪词
                name_no_ext = re.sub(r"[_\-\s]+", " ", name_no_ext).strip()
                if not name_no_ext or len(name_no_ext) < 2:
                    return {"name": p.get("name", "未命名项目"), "locked": False}
                # 更新项目名(但不锁定——内容来自文件名,聊完再确定)
                p["name"] = name_no_ext
                _projects_store.write(projects)
                return {"name": name_no_ext, "locked": False}
        return {"name": "", "locked": False}


# ─── 辅助 ───────────────────────────────────────────────────

def _has_api_key() -> bool:
    try:
        clipmind_config.get_api_key()
        return True
    except ConfigError:
        return False


def _fmt_ts(seconds):
    """格式化时间戳: 1.5 -> '0:00:01.500'"""
    if seconds is None:
        return "—"
    s = float(seconds)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:06.3f}" if h > 0 else f"{m}:{sec:06.3f}"


def _fn_label(key: str) -> str:
    """滤镜 key -> 中文标签"""
    labels = {
        "crop": "裁剪", "chromakey": "抠像", "color_grading": "调色",
        "color_preset": "调色预设", "denoise": "降噪", "stabilize": "防抖",
        "animation": "动画",
    }
    return labels.get(key, key)


def _fmt_filter(key: str, value) -> str:
    """格式化滤镜参数为可读文本"""
    if key == "crop" and isinstance(value, dict):
        return f"x={value.get('x')} y={value.get('y')} w={value.get('w')} h={value.get('h')}"
    if key == "denoise" and isinstance(value, dict):
        return f"{value.get('type', '?')} 强度={value.get('strength', '?')}"
    if key == "chromakey" and isinstance(value, dict):
        return f"{value.get('color', 'green')} 相似度={value.get('similarity', 0)} 混合={value.get('blend', 0)}"
    if key == "color_grading" and isinstance(value, dict):
        parts = []
        for k, v in value.items():
            parts.append(f"{k}={v}")
        return ", ".join(parts) if parts else str(value)
    if key == "color_preset":
        return str(value)
    if key == "animation" and isinstance(value, dict):
        atype = value.get("type", "?")
        if value.get("animations_json"):
            return f"{atype} (自定义关键帧)"
        return f"{atype}"
    return str(value)


def _find_draft_output(draft_data: dict) -> str:
    """根据草稿数据查找渲染输出文件路径"""
    # 策略 1: render_settings.output_path
    output_path = draft_data.get("render_settings", {}).get("output_path", "")
    if output_path and os.path.exists(output_path):
        return output_path

    # 策略 2: 根据 source_videos 推断(render_final 默认命名规则)
    src = draft_data.get("source_videos", [])
    if src:
        tag = hashlib.md5(src[0].encode()).hexdigest()[:8]
        default_out = os.path.join(_PROJECT_ROOT, "output", f"final_{tag}.mp4")
        if os.path.exists(default_out):
            return default_out

    # 策略 3: 在 output/ 和 drafts/{draft_id}/ 下搜索任何 mp4
    draft_id = draft_data.get("draft_id", "")
    candidates = [
        os.path.join(_PROJECT_ROOT, "output"),
        os.path.join(_PROJECT_ROOT, "drafts", draft_id),
    ]
    for cand_dir in candidates:
        if os.path.isdir(cand_dir):
            for f in os.listdir(cand_dir):
                fp = os.path.join(cand_dir, f)
                if f.endswith(('.mp4', '.mov', '.webm')) and os.path.isfile(fp):
                    return fp

    return ""


# ─── 入口 ───────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    server = RpcServer()
    write_json({"event": "ready", "version": "1.0.0"})

    try:
        server.start()
    except Exception:
        log.exception("RPC 服务致命错误")
        # 确保前端能收到错误事件
        try:
            write_json({"event": "error", "message": traceback.format_exc()})
        except Exception:
            pass  # 连写 JSON 都失败了,没法救了
    finally:
        try:
            write_json({"event": "shutdown"})
        except Exception:
            pass


if __name__ == "__main__":
    main()
