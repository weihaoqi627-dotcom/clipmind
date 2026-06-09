"""执行 Agent — Director 的士兵

每个执行 Agent 是一个独立服务:
- 接收 Director 的 mission(任务简报)+ params(具体参数)
- 自己规划 tool 调用步骤,不需要固定提示词
- 没有 max_turns,做完为止
- 返回结构化结果

两种模式:
- 分身: Director 通过 dispatch_clone 指定 tool_groups,通用去角色化
- 动效层: 硬编码白名单,画家模式(EFFECTS_LAYER_PROMPT)
"""

import os, json, time, threading
from typing import Optional, Callable
from director.logging_config import get_logger

# 预导入工具模块,确保 @tool 装饰器在工具解析前完成注册
import director.memory_store  # noqa: F401

log = get_logger("director.executor")

# ─── 工具组定义 ──────────────────────────────────────────────────
# 导演派分身时指定 tool_groups=["组名1", "组名2"]，
# 分身自动拿到该组所有工具，每组 8-15 个，不会因工具太多而找不到。
# 每个分身还自动获得通用工具（read_json/list_files等）。
# 工具组的定义集中在 tool_catalog.py 的 TOOL_CATALOG 中，这里是唯一权威来源。

# 通用工具 — 所有分身自动获得,用于自主浏览文件和读写阶段数据
_GENERIC_TOOL_NAMES = {
    "search_memory", "get_index_info", "browse_memory",
    "read_json", "list_files", "read_text",
    "show_draft",   # 分身查看草稿状态,知道裁了哪些片段
    # 阶段数据工具(每个分身都需要读写自己的阶段数据)
    "list_stage", "read_stage", "write_stage", "mark_stage_done", "get_stage_status",
}


def _get_generic_tools() -> list:
    """构建通用工具（文件浏览、索引查询），所有分身自动拥有"""
    from director.agent_loop import ToolDef

    def _read_json(path: str) -> str:
        """读取一个 JSON 文件并返回内容。分身自主浏览分析结果时使用。"""
        try:
            import json as _json
            if not os.path.exists(path):
                return f"[错误] 文件不存在: {path}"
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            return _json.dumps(data, ensure_ascii=False, indent=2)[:5000]
        except Exception as e:
            return f"[错误] 读取失败: {e}"

    def _list_files(directory: str) -> str:
        """列出目录下的所有文件（非递归），分身自主探索项目结构时使用。"""
        try:
            if not os.path.exists(directory):
                return f"[错误] 目录不存在: {directory}"
            items = os.listdir(directory)
            files = []
            for item in sorted(items):
                full = os.path.join(directory, item)
                if os.path.isfile(full):
                    size = os.path.getsize(full)
                    files.append(f"  [文件] {item} ({size/1024:.0f}KB)")
                elif os.path.isdir(full):
                    files.append(f"  [目录] {item}/")
            return "\n".join(files) if files else "(空目录)"
        except Exception as e:
            return f"[错误] 列出失败: {e}"

    def _read_text(path: str) -> str:
        """读取一个文本文件的内容（前3000字符）。分身查看日志/配置时使用。"""
        try:
            if not os.path.exists(path):
                return f"[错误] 文件不存在: {path}"
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[:3000]
        except Exception as e:
            return f"[错误] 读取失败: {e}"

    return [
        ToolDef(name="read_json", description="读取JSON文件内容，分身自主查看分析结果和项目数据",
                fn=_read_json, parameters={"type": "object", "properties": {
                    "path": {"type": "string", "description": "JSON文件的绝对路径"}},
                    "required": ["path"]}, category="通用"),
        ToolDef(name="list_files", description="列出目录下的所有文件和子目录（非递归），分身自主探索项目结构",
                fn=_list_files, parameters={"type": "object", "properties": {
                    "directory": {"type": "string", "description": "目录的绝对路径"}},
                    "required": ["directory"]}, category="通用"),
        ToolDef(name="read_text", description="读取文本文件内容（前3000字符），分身查看日志/配置/报告",
                fn=_read_text, parameters={"type": "object", "properties": {
                    "path": {"type": "string", "description": "文本文件的绝对路径"}},
                    "required": ["path"]}, category="通用"),
    ]


# 每个执行 Agent 的工具白名单（仅动效层保留）
# 动效层通过 command() 调度，有独立画家模式 prompt
EXECUTOR_TOOL_NAMES = {
    "动效层": {
        "apply_flower_text", "list_flower_texts",
        "list_available_fonts", "find_font",
        "list_gsap_eases", "preview_gsap_effect", "generate_gsap_html",
        "list_hf_blocks", "hf_snapshot", "hf_lint", "hf_validate",
        "list_hf_templates", "get_hf_template_schema",
        "generate_hf_composition", "preview_hf_template",
        "render_hf_to_draft",
        "list_emotion_animations", "search_animation",
        "get_animation_template", "build_gsap_timeline",
        "add_subtitles",
        "render_draft_preview", "check_edit",
        "show_draft", "save_draft", "list_segments",
    },
}

# 执行层注册表 — Director 动态读取,加层只需改这里
# 格式:层名 -> 一句话能力描述
LAYER_REGISTRY = {
    "动效层": "花字,动画,特效,转场,字幕,生成画面(画家模式:先构思再动手)",
}

def get_layer_registry_text() -> str:
    """生成 Director 可读的层级能力清单(仅效果层保留)"""
    lines = ["效果层(保留专用层,其他任务用 dispatch_clone 派分身):"]
    for name, desc in LAYER_REGISTRY.items():
        lines.append(f"- **{name}**: {desc}")
    lines.append("\n用 `command(\"动效层\", mission=\"...\", params={{}})` 调用.非效果层任务用 `dispatch_clone`.")
    return "\n".join(lines)

def get_layer_names_str() -> str:
    """返回所有层名(逗号分隔,用于工具描述)"""
    return "动效层"

# 动效层 — 特殊构思阶段的 prompt 注入
EFFECTS_LAYER_PROMPT = """
## 你的工作方式:画家模式(重要!)

你是一名视频动效师.你的工具是 HF + GSAP + 花字.

**关键规则:先看→再想→再说→再动手.** 不要一开始就去翻工具有什么预设.

### 第一步:看轨道素材

先用工具查看轨道上已有的素材内容:
- `show_draft()` 看完整草稿
- `list_segments()` 看片段列表
看清当前轨道上已经有什么、缺什么、哪些地方需要加效果.

### 第二步:输出方案(用文字,不调工具)

看完素材后,**先用自然语言说一遍你待会准备做什么**.必须包含:
1. 你看到了什么(当前轨道的状态,有什么效果缺口)
2. 你准备做什么(具体到:什么位置、什么效果、什么风格)
3. 你打算怎么做(用什么工具实现、分几步)

**说清楚再动手.方案没写出来之前禁止调任何工具.**

### 第三步:严格按方案执行

方案写出来后,严格按照你刚才的描述去执行:
- 一步做一件事
- 做完一步检查草稿有变化
- 如果发现方案有问题,停下来重新规划(回到第二步)

### 第四步:自评打分

做完后给自己打分:**优秀/及格/过差**.
核心判断标准只有一个:**效果像不像人做的**.具体怎么评你自己判断.

| 等级 | 处理方式 |
|---|---|
| **优秀** | 直接通过,报告完成 |
| **及格** | 说明哪里不足,做定点修复,修完直接通过 |
| **过差** | 打回重做,回到第二步重新规划 |

打完分后:
- 优秀 → 调用 `save_draft` 存草稿,确认草稿有变化,报告完成
- 及格 → 做定点修复,确认修复后存草稿,报告完成(不重评)
- 过差 → 回到第二步重新规划,重做

### 完成标准(防创意空转)

- **每个位置最多试 3 种方案.** 第 3 种方案做完后,选最好的那个交差
- "还不错"就是完成信号.不需要完美
- 不要回头优化
- 如果 3 种方案都不满意——交相对最好的那个,标注"建议人工调整"
- **草稿没变=空转.** 每次操作后确认草稿有变化,没有变化立即停止
"""


def _build_executor_prompt(
    agent_type: str,
    mission: str,
    params: dict,
    pipeline,
    done_when: str = "",
    tool_groups: list = None,
) -> str:
    """为执行 Agent 构建 system prompt

    两种模式:
    - 动效层: 保留角色+EFFECTS_LAYER_PROMPT(画家模式)
    - 其他/通用(dispatch_clone): 去角色化,只给任务描述+工具列表,由 Director 指定工具
    """

    # 构建共享上下文(素材路径等)
    vp = getattr(getattr(pipeline, 'state', None), 'video_paths', [])
    draft_id = getattr(getattr(pipeline, 'state', None), 'draft_id', 'unknown')
    material_paths = "\n".join(f"  - {p}" for p in vp) if vp else "  (无素材)"
    shared_context = f"""
## 素材上下文
素材路径:
{material_paths}

工作目录: {getattr(pipeline, 'work_dir', 'unknown')}
草稿 ID: {draft_id}
"""

    if agent_type == "动效层":
        # 效果层保留角色模式 + 画家模式
        prompt = f"""你是 ClipMind 的动画师.

你的任务由导演(Director)下达.导演告诉你要修什么,补什么效果,
你来决定具体怎么做.

{mission}

{json.dumps(params, ensure_ascii=False, indent=2) if params else ""}

{shared_context}
- 画面上一次看过的内容已在分析报告中记录
- 这个片段已经被编排锁定(picture locked),不要重排顺序
"""
        prompt += "\n" + EFFECTS_LAYER_PROMPT
    else:
        # 通用分身模式:去角色化,Director 在 mission 中告诉分身要做什么、用什么工具
        done_section = f"\n## 完成标准\n{done_when}\n" if done_when else ""
        group_section = ""
        if tool_groups:
            group_section = f"\n你携带的工具分组: **{'、'.join(tool_groups)}**\n"
        prompt = f"""你是 ClipMind 的执行分身,被导演派来执行一个具体任务.
{group_section}
你的导演已经把完整信息写在了下面的任务描述中.
**先用任务描述里的数据干活,不要回头翻原始分析文件.**

如果你发现任务描述里的数据不够,用 list_files/read_json 补充需要的信息,
但优先相信 mission 里的数据——它已经包含了上游角色产出的结果.

## 当前任务
{mission}

## 可用参数
{json.dumps(params, ensure_ascii=False, indent=2) if params else "(无额外参数)"}
{done_section}
{shared_context}
"""

    # 通用结尾
    prompt += """
## 铁律

**单工具规则(重要!):** 如果你只有 1 个工具可用,调完它就完成了.
直接报告结果并写"完成".不要思考"还有没有其他事要做"——你的任务就是这个工具做的事.

**不要重复调用.** 同一个工具连续调用 2 次后还没进展,说明卡住了,立刻报告并结束.

**只看最新草稿.** `show_draft()` 已经给你全部需要的信息,不需要读旧版本(v1/v2/v3...).
你只需要关心最新版本的片段列表.旧版本包含的是历史中间状态,看了只会让你困惑.

**batch_analyze 只调一次.** 看返回的摘要就够了.想看详情用 search_memory.
禁止重复调 batch_analyze.

每一次工具调用后,检查该工具是否改变了草稿:
- batch_analyze: 不操作草稿(操作的是索引),跳过检查,直接认为是有效的
- 其他工具: 草稿有变化 -> 继续; 草稿没变 -> 空转,停止并报告"空转无变化"

## 工具使用规则

- **cut_segment**: 调用时必须传 draft_id,值取导演指定的 draft_id(如 "main").
  不传 draft_id 则裁切不入草稿,后续无法编排.不要重复裁切同一段.
- **discard_segment(seg_id)**: seg_id 是字符串(如 "seg_008"),不是整数.
- **mark_discard(video_path, clip_id)**: clip_id 是整数,只用于原始素材标记,
  不适用于已裁切片段.已裁切片段用 discard_segment.
- **screen_clip**: 只在浏览器环境可用(CLI 不可用),调用前先检查环境.
- **reorder_draft_segments**: 需要草稿已存在(之前调 cut_segment 时传了 draft_id 才算).
- **get_asr_transcript 已知问题**: batch_analyze 之后此工具返回"未检测到语音",
  因为使用了不同的语音模型.语音内容已由 batch_analyze 存入索引.
  **不要调 get_asr_transcript**,用 search_memory("语音"/"ASR"/关键词) 查索引.
- **batch_analyze 禁止重复调用**: 一次分析已包含所有片段的画面+语音分析,
  结果存索引.禁止再次调 batch_analyze.想看详情用 search_memory.

## 逃逸通道(卡住了就报告)

如果你试了 3 种不同方案都无法继续,直接报告:
- 你尝试过什么
- 卡在哪里
- 还缺什么信息/工具

写 "卡住了:……" 然后结束.**不要死磕.Partial results > no results.**

完成时,在最后一条消息中写"完成".不要单独发一条文字总结——工具调用结果本身就是你的工作记录.

现在开始执行任务.第一步就调用工具.
"""
    return prompt


def _get_executor_tools(agent_type: str, pipeline, tool_names: Optional[list] = None,
                        tool_groups: list = None) -> list:
    """获取执行 Agent 可用的工具列表

    优先规则: tool_groups > tool_names > 动效层白名单

    Args:
        agent_type: 执行 Agent 类型(动效层有硬编码白名单)
        pipeline: MultiStagePipeline 实例
        tool_names: Director 指定的工具名列表
        tool_groups: 工具分组名列表，如 ["画面与场景", "裁切与提取"]
                     分身自动获得这些组的所有工具（每组 8-15 个）
    """
    from director.registry import get_tools_by_phase
    from director.agent_loop import ToolDef
    from director.tool_catalog import TOOL_CATALOG, get_tools_by_group as _get_group_tools
    _EXECUTOR_PHASES = ("analyze", "plan", "edit", "render", "all")

    # 1. 收集所有候选工具
    all_tools = get_tools_by_phase(*_EXECUTOR_PHASES)

    if tool_groups:
        # 新模式: 按工具分组取工具
        tool_names_set = set()
        for group in tool_groups:
            group_tools = _get_group_tools(group)
            if group_tools:
                tool_names_set.update(group_tools)
            else:
                log.warning("未知工具分组 '%s', 可用分组: %s",
                           group, ", ".join(k for k in TOOL_CATALOG if not k.startswith("花字")))
    elif tool_names is not None:
        # Director 指定工具列表
        tool_names_set = set(tool_names)
    else:
        # 动效层白名单
        tool_names_set = EXECUTOR_TOOL_NAMES.get(agent_type, set())

    # 2. 加通用工具（所有分身都能自主浏览文件）
    tool_names_set |= _GENERIC_TOOL_NAMES

    # 3. 构建 ToolDef
    tools_data = [t for t in all_tools if t["name"] in tool_names_set]
    tool_defs = []
    for t in tools_data:
        td = ToolDef(
            name=t["name"],
            description=t.get("description", ""),
            fn=t.get("fn"),
            parameters=t.get("parameters", {}),
        )
        tool_defs.append(td)

    # 4. 补充通用工具的 ToolDef（不在 registry 里）
    existing_names = {t.name for t in tool_defs}
    for gt in _get_generic_tools():
        if gt.name not in existing_names:
            tool_defs.append(gt)

    return tool_defs


def run_executor(
    agent_type: str,
    mission: str,
    params: dict,
    pipeline,
    tool_names: Optional[list] = None,
    tool_groups: list = None,
    done_when: str = "",
    verbose: bool = False,
    on_event: Optional[Callable] = None,
    is_cancelled: Optional[Callable] = None,
) -> dict:
    """调度一个执行 Agent

    Args:
        agent_type: 执行 Agent 类型(动效层用"动效层",其他用"分身")
        mission: 导演写的任务简报
        params: 任务参数(时间范围,素材路径等)
        pipeline: MultiStagePipeline 实例
        tool_names: Director 指定的工具名列表
        tool_groups: 工具分组名列表，如 ["画面与场景", "裁切与提取"]
                     分身自动获得这些组的所有工具（每组 8-15 个）
        done_when: 完成标准描述(非动效层任务使用)
        verbose: 是否打印详细日志
        on_event: 事件回调
        is_cancelled: 取消检查函数

    Returns:
        {"completed": bool, "summary": str, "turns": int, "error": str}
    """
    from director.agent_loop import agent_loop
    from director.pipeline import _make_llm_func

    stream_cb = None
    if on_event:
        stream_cb = lambda c: on_event("stream_chunk", {"content": c})
    llm_func = _make_llm_func(stream_callback=stream_cb)

    # 构建 system prompt(带 done_when)
    system_prompt = _build_executor_prompt(agent_type, mission, params, pipeline, done_when, tool_groups)

    # 获取工具(按 tool_groups 或 tool_names)
    tools = _get_executor_tools(agent_type, pipeline, tool_names, tool_groups)

    if verbose:
        log.info("执行 Agent [%s] 启动: mission=%s...",
                 agent_type, mission[:100] if mission else "(空)")

    # 过滤 complete 事件,防止 executor 的完成事件穿透到上级管线
    _filtered_on_event = None
    if on_event:
        _on_event_ref = on_event
        def _filter_executor_event(e, d):
            if e == "complete":
                return
            _on_event_ref(e, d)
        _filtered_on_event = _filter_executor_event

    start = time.time()
    try:
        state = agent_loop(
            system_prompt=system_prompt,
            task=mission,
            tools=tools,
            llm_func=llm_func,
            max_turns=101,  # 故事要多少镜头就剪多少,不做硬限制
            verbose=True,
            on_event=_filtered_on_event,
            is_cancelled=is_cancelled,
            require_render=False,
        )

        elapsed = time.time() - start
        summary = ""
        if state.final_reasoning:
            summary = state.final_reasoning[:3000]

        # 自动持久化分身执行记录到 _index/ (供后续 search_memory 检索)
        try:
            from director.memory_store import save_delegate_entities
            work_dir = getattr(pipeline, 'work_dir', '')
            if work_dir:
                tool_history = [(t, n, r) for t, n, r in state.history]
                entities = [
                    {"tool": name, "result": result, "turn": turn}
                    for turn, name, result in tool_history
                ]
                entities.append({
                    "tool": "final_report",
                    "result": summary,
                })
                save_delegate_entities(work_dir, mission, entities, agent_type)
        except Exception as e:
            log.warning("分身实体持久化失败(不影响执行结果): %s", e)

        if verbose:
            log.info("执行 Agent [%s] 完成: %d turns, %.1fs", agent_type, state.turns_used, elapsed)
        log.info("[执行Agent] %s: %d turns, %.1fs, 完成=%s, 摘要=%s",
                 agent_type, state.turns_used, elapsed,
                 not state.force_stop,
                 summary[:200] if summary else "(空)")

        # state.error 可能由 agent_loop 内部设置（如 LLM 调用失败后设的），
        # 不能直接写死 "" —— 否则 dispatch_clone 那边拿到空错误不知道发生了什么。
        actual_error = getattr(state, 'error', '') or ''
        return {
            "completed": not state.force_stop,
            "summary": summary,
            "turns": state.turns_used,
            "elapsed": round(elapsed, 1),
            "error": actual_error,
            "force_stop": state.force_stop,
        }

    except Exception as e:
        elapsed = time.time() - start
        log.exception("执行 Agent [%s] 异常", agent_type)
        # 异常时也保存部分记录
        try:
            from director.memory_store import save_delegate_entities
            work_dir = getattr(pipeline, 'work_dir', '')
            if work_dir:
                save_delegate_entities(work_dir, mission,
                    [{"tool": "error", "result": f"{type(e).__name__}: {str(e)[:500]}"}],
                    agent_type)
        except Exception:
            pass
        return {
            "completed": False,
            "summary": "",
            "turns": 0,
            "elapsed": round(elapsed, 1),
            "error": f"{type(e).__name__}: {str(e)[:500]}",
            "force_stop": False,
        }
