"""
Agent Loop — 核心循环
=====================
标准 while-true 循环,和 Claude Code SDK / OpenAI Codex CLI 一样的架构.

流程:
  用户输入 -> LLM 推理 -> 调工具?-> 执行工具 -> 结果写回 -> 下一个 turn
                       ↓ 否
                    说"完成"了?-> 返回结果
                       ↓ 否
                    推回:"调用工具"

核心原则:
- **模型必须通过原生 function calling 调工具**.写文本不算.
- 不回退,不解析文本,不做文本->函数转换.
- 工具结果自动序列化(长 JSON 压短),省 token.

变更记录:
  2026-06-01: 添加连续工具失败检测 + 自动终止
"""
import json, time, copy, os
from typing import Any, Callable, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _ToolTimeout


# ─── Tool 定义 ──────────────────────────────────────────────

class ToolDef:
    """一个可调用的工具"""
    def __init__(self, name: str, description: str, fn: Callable,
                 parameters: dict = None, category: str = ""):
        self.name = name
        self.description = description
        self.fn = fn
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.category = category  # 所属部门分类,如"素材分析部"

    def to_openai_tool(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }}

    def __call__(self, **kwargs) -> Any:
        return self.fn(**kwargs)


# ─── Agent State ────────────────────────────────────────────

class AgentState:
    """跨 turn 持久化状态."""
    def __init__(self, **kwargs):
        self._data = dict(kwargs)
        self.history = []          # [(turn, tool_name, summary)]
        self.tool_issues = []      # [{"tool": name, "error": msg}]
        self.turns_used = 0
        self.force_stop = False
        self.final_reasoning = ""
        self.error = ""

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return self._data.get(name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            self._data[name] = value


# ─── 结果序列化(压缩长结果省 token)─────────────────────────

def _reserialize_result(result: Any, tool_name: str = "") -> str:
    """将工具结果压缩为紧凑摘要.短结果不动,长结果摘要."""
    if result is None:
        return "(空)"
    if isinstance(result, bool):
        return "成功" if result else "失败"

    s = str(result)
    if len(s) <= 500:
        return s

    try:
        data = json.loads(s)
        return _summarize_json(data)
    except (json.JSONDecodeError, TypeError):
        pass

    return s[:500] + f"\n...({len(s)} 字符,截断)"


def _summarize_json(data) -> str:
    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            if isinstance(v, list):
                parts.append(f"{k}={len(v)}项")
            elif isinstance(v, (int, float)):
                parts.append(f"{k}={v}")
            elif isinstance(v, str) and len(v) > 80:
                parts.append(f"{k}={v[:80]}...")
            else:
                parts.append(f"{k}={v}")
        s = ", ".join(parts)
        return s[:800] + "..." if len(s) > 800 else s

    if isinstance(data, list):
        total = len(data)
        if total == 0:
            return "空列表"
        preview = data[:5]
        item_strs = []
        for item in preview:
            if isinstance(item, dict):
                fields = {k: v for k, v in list(item.items())[:5]}
                item_strs.append(str(fields))
            else:
                item_strs.append(str(item)[:80])
        summary = f"[{total}项] " + " | ".join(item_strs)
        if total > 5:
            summary += f" ...(还有{total-5}项)"
        return summary[:800] + "..." if len(summary) > 800 else summary

    return str(data)[:500]


# ─── 工具结果检测 ──────────────────────────────────────────

# 工具结果中这些关键词出现 3+ 次连续 -> 判定为 stuck,自动终止
_STUCK_KEYWORDS = [
    "不存在", "未找到", "找不到",
    "not found", "doesn't exist", "not exist",
    "失败", "fail", "error",
    "无效", "invalid",
]


def _is_error_result(result_str: str) -> bool:
    """判断工具结果是否是错误/失败"""
    lower = result_str.lower()
    return any(kw in lower for kw in _STUCK_KEYWORDS)


# ─── Agent Loop ─────────────────────────────────────────────

MAX_TURNS = 80
RESULT_LIMIT = 20000  # 批量分析结果可能很长,放宽到 20000 字符
TOOL_TIMEOUT = 1800  # batch_analyze 等耗时长,设 30 分钟

# 连续失败多少次自动终止
MAX_CONSECUTIVE_FAILURES = 5

# 重复工具检测参数
MAX_REPEAT_COUNT = 3      # 同一工具+相同参数重复 N 次 -> 拦截
REPEAT_WINDOW_SIZE = 6    # 检测窗口(最近 N 次工具调用)
FINISH_KEYWORDS = {"完成", "done", "结束", "完事", "好了", "卡住了"}


def agent_loop(
    system_prompt: str,
    task: str,
    tools: list[ToolDef],
    llm_func: Callable,
    state: AgentState = None,
    max_turns: int = MAX_TURNS,
    verbose: bool = True,
    on_event: Optional[Callable] = None,
    is_cancelled: Optional[Callable] = None,
    history: list[dict] = None,
    require_render: bool = True,
    min_tool_calls: int = 0,
) -> AgentState:
    """
    核心循环:LLM -> 工具 -> 结果 -> LLM -> ... 直到任务完成.

    Args:
        system_prompt: AI 人格/规则
        task: 本次任务
        tools: 可用工具列表
        llm_func: fn(messages, openai_tools) -> {content, tool_calls}
        state: 初始状态
        max_turns: 最大轮数
        verbose: 打印日志
        on_event: 事件回调 fn(event_name, data)
        is_cancelled: 取消检查 fn() -> bool
        history: 注入在 system 和 task 之间的历史消息
        require_render: True=AI说完成前必须先调过渲染工具
        min_tool_calls: AI说完成前至少调过的工具数量(防早退).
                        0=不检查.例如管线设为2(split+analyze)确保不跳过分析.
    """
    if state is None:
        state = AgentState()

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": task})

    openai_tools = [t.to_openai_tool() for t in tools]
    tool_map = {t.name: t for t in tools}

    if verbose:
        tools_str = ", ".join(t.name for t in tools)
        print(f"\n{'='*60}")
        print(f"Agent Loop | {len(tools)} 个工具: {tools_str} | max_turns={max_turns}")
        print(f"{'='*60}")

    consecutive_errors = 0
    _recent_calls = []       # [(tool_name, args_hash)] — 滚动窗口,防空转

    with ThreadPoolExecutor(max_workers=8) as executor:
        for turn in range(1, max_turns + 1):
            # ── 取消检查 ──
            if is_cancelled and is_cancelled():
                if verbose:
                    print(f"\n⏹ 取消(第 {turn} 步)")
                if on_event:
                    on_event("progress", {"status": "cancelled", "turns": turn})
                state.turns_used = turn
                state.force_stop = True
                return state

            if verbose:
                print(f"\n── Turn {turn}/{max_turns} ──")

            # ── LLM 推理 ──
            try:
                response = llm_func(messages, openai_tools)
            except Exception as e:
                if verbose:
                    print(f"  ❌ LLM 调用失败: {e}")
                import time as _t
                _t.sleep(2)
                try:
                    response = llm_func(messages, openai_tools)
                except Exception as e2:
                    if verbose:
                        print(f"  ❌ 重试也失败: {e2}")
                    state.error = str(e2)
                    break

            # ── 有工具调用 -> 执行 ──
            if response.tool_calls and len(response.tool_calls) > 0:
                unknown_count = 0
                all_errors = True  # 本 turn 所有调用都失败?

                for call in response.tool_calls:
                    tool_name = call.function.name
                    tool_fn = tool_map.get(tool_name)

                    if tool_fn is None:
                        unknown_count += 1
                        if verbose:
                            print(f"  ⚠ 未知工具: {tool_name},跳过")
                        continue

                    try:
                        args = json.loads(call.function.arguments) if call.function.arguments else {}
                    except json.JSONDecodeError:
                        if verbose:
                            print(f"  ⚠ 参数解析失败: {tool_name}")
                        continue

                    if verbose:
                        args_str = ", ".join(f"{k}={v}" for k, v in args.items())
                        print(f"  🔧 {tool_name}({args_str})")
                    if on_event:
                        on_event("tool_start", {"name": tool_name, "args": args})

                    # ── 重复工具调用检测 ──
                    args_hash = json.dumps(args, sort_keys=True, ensure_ascii=False)[:200]
                    _recent_calls.append((tool_name, args_hash))
                    if len(_recent_calls) > REPEAT_WINDOW_SIZE:
                        _recent_calls.pop(0)
                    repeat_count = sum(
                        1 for t, h in _recent_calls
                        if t == tool_name and h == args_hash
                    )

                    if repeat_count >= MAX_REPEAT_COUNT:
                        raw_result = (
                            f"[系统] 重复工具调用检测:\"{tool_name}\" 相同参数已连续调用 "
                            f"{repeat_count} 次.请停止重复,换一种方案."
                        )
                        state.tool_issues.append({"tool": tool_name, "error": "重复调用"})
                        elapsed = 0
                        if verbose:
                            print(f"  ⛔ 重复拦截: {tool_name}")

                    else:
                        start_t = time.time()
                        try:
                            fut = executor.submit(tool_fn, **args)
                            raw_result = fut.result(timeout=TOOL_TIMEOUT)
                        except _ToolTimeout:
                            raw_result = f"[超时] {tool_name} > {TOOL_TIMEOUT}s"
                            state.tool_issues.append({"tool": tool_name, "error": "Timeout"})
                            if verbose:
                                print(f"  ⏱ 超时: {tool_name}")
                        except Exception as e:
                            raw_result = f"[错误] {type(e).__name__}: {e}"
                            state.tool_issues.append({"tool": tool_name, "error": str(e)[:300]})
                            if verbose:
                                print(f"  ❌ 错误: {e}")
                        elapsed = time.time() - start_t

                    result_str = _reserialize_result(raw_result, tool_name)
                    if len(result_str) > RESULT_LIMIT:
                        result_str = result_str[:RESULT_LIMIT] + "..."

                    if verbose:
                        print(f"  ✅ ({elapsed:.1f}s)  {result_str[:150]}")
                    if on_event:
                        on_event("tool_end", {"name": tool_name, "result": result_str[:500], "elapsed": round(elapsed, 1)})

                    state.history.append((turn, tool_name, result_str[:200]))

                    # 检测是否成功
                    if not _is_error_result(result_str):
                        all_errors = False

                    # 结果写回对话
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [{
                            "id": call.id,
                            "type": "function",
                            "function": {"name": tool_name, "arguments": call.function.arguments}
                        }]
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result_str,
                    })

                # 全部工具都未知 -> 提前终止
                if unknown_count == len(response.tool_calls):
                    if unknown_count >= 3:
                        state.error = f"连续 {unknown_count} 轮工具都未注册"
                        state.force_stop = True
                        break
                    consecutive_errors += 1
                elif all_errors:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0

                # 连续失败超过阈值 -> 终止
                if consecutive_errors >= MAX_CONSECUTIVE_FAILURES:
                    if verbose:
                        print(f"\n⏹ 连续 {MAX_CONSECUTIVE_FAILURES} 次工具调用失败,自动终止")
                    state.error = f"工具连续失败 {MAX_CONSECUTIVE_FAILURES} 次"
                    state.force_stop = True
                    break

                continue  # 下一轮

            # ── 没有工具调用 ──
            content = response.content or ""
            says_done = any(kw in content for kw in FINISH_KEYWORDS)

            if require_render:
                has_rendered = any(h[1] in ("render_final", "render_from_draft") for h in state.history)
                is_finish = has_rendered and says_done
            elif min_tool_calls > 0:
                total_calls = len(state.history)
                is_finish = says_done and total_calls >= min_tool_calls
                if says_done and not is_finish:
                    need_more = min_tool_calls - total_calls
                    push_msg = (
                        f"[系统] 你还不能结束,当前只调了 {total_calls} 次工具,"
                        f"至少需要 {min_tool_calls} 次.还需要完成以下步骤:\n"
                        f"1. batch_analyze 分析所有片段\n"
                        f"2. 编排素材\n"
                        f"继续执行,不要输出文字."
                    )
                    messages.append({"role": "user", "content": push_msg})
                    if verbose:
                        print(f"  ⛔ 早退拦截: 仅 {total_calls}/{min_tool_calls} 次工具调用")
                    continue
            else:
                is_finish = says_done

            if is_finish:
                if verbose:
                    print(f"\n✅ 完成(第 {turn} 步)")
                if on_event:
                    on_event("ai_message", {"content": content, "done": True})
                    on_event("complete", {"turns": turn})
                state.final_reasoning = content
                state.turns_used = turn
                return state

            if verbose:
                if content:
                    print(f"  AI: {content[:200]}...")
                print(f"  ↻ 没有调工具,推回")
            if on_event and content:
                on_event("ai_message", {"content": content, "done": False})
            messages.append({
                "role": "user",
                "content": "调用工具.不要写文字回复."
            })

    # ── 超出 max_turns ──
    if verbose:
        print(f"\n⚠ 达到最大 turn 数 ({max_turns})")
    if on_event:
        on_event("complete", {"turns": max_turns, "forced": True})
    state.turns_used = max_turns
    state.force_stop = True
    return state
