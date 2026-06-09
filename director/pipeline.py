"""
素材分析管线 — 并行分析 + 执行 Agent 底座

不再包含固定 5 阶段框架.Director 通过 `command` 工具动态调度执行 Agent,
执行 Agent 直接调用 `agent_loop` 完成具体任务.

保留内容:
  - MultiStagePipeline 类(作为工作目录 + 状态的基础设施)
  - _make_llm_func()(执行 Agent 调用 LLM 用)
  - scene_detect() / group_into_chunks()(保留备用,不再被管线自动调用)

注意:旧版 _parallel_material_analysis() 已删除——Director 通过 delegate + split_by_scenes + batch_analyze 驱动分析流程.
"""
import sys, os, json, time, re, subprocess, threading
from pathlib import Path

from .logging_config import get_logger
from .exceptions import ConfigError, PipelineError

log = get_logger("director.pipeline")

PROJECT_DIR = Path(__file__).parent.parent


from director.workspace import _sanitize_path_name, get_project_dir


# ═══════════════════════════════════════════════════════════
#  用量上报
# ═══════════════════════════════════════════════════════════

def _report_llm_usage(model: str, tokens_in: int, tokens_out: int):
    """上报 LLM 调用用量到 ClipMind 后端(fire-and-forget,不阻塞主流程)"""
    if not tokens_in and not tokens_out:
        return
    backend_url = None
    api_key = None
    try:
        from server.director_runner import DirectorRunner
        tl = DirectorRunner._thread_local
        backend_url = getattr(tl, 'backend_url', '') or ''
        api_key = getattr(tl, 'report_api_key', '') or ''
    except Exception:
        pass
    if not backend_url:
        backend_url = os.environ.get("CLIPMIND_BACKEND_URL", "")
    if not api_key:
        api_key = os.environ.get("CLIPMIND_REPORT_API_KEY", "")
    if not backend_url or not api_key:
        return  # 没配后端地址或上报 key 就不报
    try:
        import httpx
        httpx.post(
            f"{backend_url.rstrip('/')}/api/user/usage/report",
            json={"model": model, "tokens_in": tokens_in, "tokens_out": tokens_out},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5.0,
        )
        log.debug("用量上报: %s in=%d out=%d", model, tokens_in, tokens_out)
    except Exception as e:
        log.debug("用量上报失败(不影响主流程): %s", e)


# ═══════════════════════════════════════════════════════════
#  LLM 调用工厂
# ═══════════════════════════════════════════════════════════

def _make_llm_func(stream_callback=None):
    """构建 LLM 调用函数.优先用线程本地存储(runner 已设),回退到环境变量,再回退到 config 模块.

    Args:
        stream_callback: 可选,传入后会在每次收到 LLM 流式块时调用 callback(chunk_text)
    """
    def llm_call(messages, tools):
        nonlocal stream_callback
        # 决定 API 配置:优先线程本地,其次环境变量,最后 config 模块
        try:
            # 检查当前线程的本地存储
            from server.director_runner import DirectorRunner
            tl = DirectorRunner._thread_local
            base_url = getattr(tl, 'base_url', '') or ''
            api_key = getattr(tl, 'api_key', '') or ''
            model = getattr(tl, 'model', '') or ''
        except Exception:
            base_url = ''
            api_key = ''
            model = ''

        # 线程本地没有 -> 回退环境变量
        if not api_key:
            api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not base_url:
            base_url = os.environ.get("LLM_BASE_URL",
                        "https://dashscope.aliyuncs.com/compatible-mode/v1")
        if not model:
            model = os.environ.get("LLM_MODEL", "qwen3.6-plus")

        # 环境变量也没有 -> 回退 config 模块(from . import config as clipmind_config)
        if not api_key:
            try:
                from . import config as clipmind_config
                api_key = clipmind_config.get_api_key()
                if not base_url:
                    base_url = clipmind_config.get_base_url()
                if not model:
                    model = clipmind_config.get_model()
            except ConfigError as e:
                raise ValueError(f"API 密钥未配置: {e}") from e

        if not api_key:
            raise ValueError("API 密钥未配置:请设置 DASHSCOPE_API_KEY 环境变量,"
                             " LLM_API_KEY 或 config.json 中的 api_key")

        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)

        # 构建工具定义(OpenAI 格式)
        # agent_loop 传过来的 tools 已经是 dict 列表了(to_openai_tool 转换过的),
        # 但也可能传 ToolDef 对象列表(直接调用 llm_func 时).两种都处理.
        openai_tools = []
        for t in (tools or []):
            if hasattr(t, 'to_openai_tool'):
                # ToolDef 对象 -> 转 dict
                openai_tools.append(t.to_openai_tool() if callable(t.to_openai_tool) else t.to_openai_tool)
            elif isinstance(t, dict):
                # 已经是 dict 格式了,直接用
                openai_tools.append(t)

        kwargs = dict(
            model=model,
            messages=messages,
            tools=openai_tools or None,
            stream=True,
            stream_options={"include_usage": True},  # 请求 final chunk 返回用量
        )

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            err_str = str(e)
            # 如果是递归重试,直接抛
            if 'overdue-payment' in err_str:
                raise
            if 'Arrearage' in err_str:
                raise
            # 如果 stream_options 不被兼容 API 支持,移除后重试
            if ('unknown parameter' in err_str.lower()
                    or 'unexpected' in err_str.lower()
                    or 'stream_options' in err_str.lower()):
                log.warning("stream_options 不被 API 支持,移除后重试")
                kwargs.pop('stream_options', None)
                response = client.chat.completions.create(**kwargs)
            else:
                log.warning("LLM 首次调用失败: %s,重试中...", err_str[:200])
                import time as _t
                _t.sleep(1)
                response = client.chat.completions.create(**kwargs)

        # 处理流式响应
        full_content = ""
        tool_calls_buffer = {}
        choice = None
        last_chunk = None  # 追踪最后一个 chunk 以获取用量

        for chunk in response:
            last_chunk = chunk  # 捕获每个 chunk（包括用量-only chunk）
            if stream_callback and chunk.choices:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    stream_callback(delta.content)

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue

            # 累积文本
            if delta.content:
                full_content += delta.content

            # 累积 tool_calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_buffer:
                        tool_calls_buffer[idx] = {
                            "id": tc.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.function:
                        if tc.function.name:
                            tool_calls_buffer[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_buffer[idx]["function"]["arguments"] += tc.function.arguments

            choice = chunk.choices[0]

        # ── 用量上报(从 final chunk 提取) ──
        if last_chunk is not None:
            try:
                usage_info = getattr(last_chunk, 'usage', None)
                if usage_info is not None:
                    tokens_in = getattr(usage_info, 'prompt_tokens', 0) or getattr(usage_info, 'input_tokens', 0) or 0
                    tokens_out = getattr(usage_info, 'completion_tokens', 0) or getattr(usage_info, 'output_tokens', 0) or 0
                    if tokens_in or tokens_out:
                        _report_llm_usage(model, tokens_in, tokens_out)
            except Exception:
                pass  # 上报失败不影响主流程

        # 组装最终响应
        finish_reason = choice.finish_reason if choice else "stop"

        class LLMResponse:
            pass

        resp = LLMResponse()
        resp.content = full_content
        resp.tool_calls = None
        resp.finish_reason = finish_reason

        if tool_calls_buffer:
            resp.tool_calls = []
            for idx in sorted(tool_calls_buffer.keys()):
                tc_data = tool_calls_buffer[idx]
                class ToolCall:
                    pass
                tc = ToolCall()
                tc.id = tc_data["id"]
                tc.type = "function"
                tc.function = type('func', (), {})()
                tc.function.name = tc_data["function"]["name"]
                tc.function.arguments = tc_data["function"]["arguments"]
                resp.tool_calls.append(tc)

        return resp

    return llm_call


# ═══════════════════════════════════════════════════════════
#  场景检测 & Chunk 分组(并行素材分析用)
# ═══════════════════════════════════════════════════════════

def scene_detect(video_path: str, threshold: float = 0.3) -> list[float]:
    """ffmpeg 快速场景检测,返回所有场景切换点的时间戳(秒)."""
    cmd = [
        "ffmpeg", "-i", video_path,
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
    except subprocess.TimeoutExpired:
        log.warning("scene_detect 超时: %s", os.path.basename(video_path))
        return []

    if r.stderr is None:
        log.warning("scene_detect stderr 为空 (视频: %s)", os.path.basename(video_path))
        return []

    scenes = [0.0]
    for line in r.stderr.split("\n"):
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            if t > 0 and (not scenes or abs(t - scenes[-1]) > 0.5):
                scenes.append(t)
    return scenes


def group_into_chunks(scene_timestamps: list[float],
                       min_dur: float = 600,
                       max_dur: float = 900) -> list[tuple[float, float]]:
    """将场景时间戳分组为 10-15 分钟的 chunk."""
    if not scene_timestamps or len(scene_timestamps) <= 1:
        return [(0.0, max(scene_timestamps[-1], max_dur) if scene_timestamps else max_dur)]

    total_dur = scene_timestamps[-1]
    if total_dur <= max_dur:
        return [(0.0, total_dur)]
    chunks = []
    cursor = 0.0

    while cursor < total_dur:
        chunk_end = min(cursor + max_dur, total_dur)
        best_cut = chunk_end
        for t in scene_timestamps:
            if t > cursor + min_dur and t < chunk_end:
                best_cut = t
        if total_dur - best_cut < min_dur * 0.5 and len(chunks) > 0:
            chunks[-1] = (chunks[-1][0], total_dur)
            break
        chunks.append((cursor, best_cut))
        cursor = best_cut
        if cursor >= total_dur:
            break
    return chunks


def _fmt_time(seconds: float) -> str:
    """秒 -> mm:ss / hh:mm:ss"""
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def get_video_duration(video_path: str) -> float:
    """获取视频时长(秒).使用 ffmpeg -i 解析,兼容 Windows ffprobe 缺陷."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=30,
        )
        stderr = r.stderr.decode('utf-8', errors='replace')
        m = re.search(r'Duration: (\d+):(\d+):(\d+\.?\d*)', stderr)
        if m:
            h, min_, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
            return h * 3600 + min_ * 60 + s
        log.warning("get_video_duration: 未解析到时长 %s", os.path.basename(video_path))
        return 0.0
    except FileNotFoundError:
        log.error("get_video_duration: ffmpeg 未安装")
        return 0.0
    except Exception as e:
        log.warning("get_video_duration 失败 %s: %s", os.path.basename(video_path), e)
        return 0.0


# ═══════════════════════════════════════════════════════════
#  Pipeline 基础设施
# ═══════════════════════════════════════════════════════════

class MultiStagePipeline:
    """管线实例.Director 通过此实例访问工作目录,状态,并行分析能力.

    不再包含固定阶段(Stage1~5),不再有 run() / run_stage() 方法.
    Director 通过 command 工具动态调度执行 Agent,执行 Agent 自行调用 agent_loop.
    管线实例只做:工作区管理,状态持久化,并行素材分析.
    """

    _thread_local = threading.local()

    def __init__(
        self,
        video_paths: list[str],
        task: str = "",
        llm_func=None,
        work_dir: str = "",
        verbose: bool = False,
        on_event: callable = None,
        director_brief: str = "",
        project_name: str = "",
    ):
        from director.entry import _load_all_tools
        _load_all_tools()

        self.video_paths = video_paths
        self.task = task
        self.llm_func = llm_func or _make_llm_func()
        self.verbose = verbose
        self.on_event = on_event or (lambda e, d: None)

        # 工作目录 — 固定 workspace 路径，不扫描、不猜测
        if work_dir:
            self.work_dir = os.path.abspath(work_dir)
        else:
            self.work_dir = get_project_dir(project_name or "default")

        # 环境变量传递工作目录给子工具（所有线程继承进程级 env var）
        MultiStagePipeline._thread_local.pipeline_dir = self.work_dir
        os.environ["CLIPMIND_PIPELINE_DIR"] = self.work_dir

        # 管线状态
        from director.pipeline_state import PipelineState
        self.state = PipelineState(self.work_dir)
        self.state.user_task = task
        self.state.video_paths = [os.path.abspath(p) for p in video_paths]
        self.state.save()

        self.start_time = None

        if director_brief:
            self.state.director_brief = director_brief
            self.state.save()

    # ─── 状态 ─────────────────────────────────────────

    def _reload_state(self):
        """从磁盘重新加载管线状态(其他模块可能修改了 state.json)"""
        from director.pipeline_state import PipelineState
        self.state = PipelineState(self.work_dir)
