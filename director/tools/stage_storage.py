"""
阶段数据存储 — 分级存储管线各阶段的输入输出

架构:
  stage_data/
  ├── 01_cut/input/        ← Director写入: 裁切素材清单
  ├── 01_cut/output/       ← 裁切师写入: 片段列表
  ├── 02_arrange/input/    ← Director搬运: 裁切结果
  ├── 02_arrange/output/   ← 编排师写入: 时间线
  ├── 03_audio/input/
  ├── 03_audio/output/
  ├── 04_effects/input/
  ├── 04_effects/output/
  └── pipeline.json        ← 管线状态追踪

规则:
  - 分身启动后先读自己阶段的 input,不看别的阶段
  - 写完 output 就结束,Director 负责搬运到下一阶段 input
  - 重做某阶段:清 output,下游全部等
"""

import os
import json

from director.registry import tool

STAGE_DATA = "stage_data"

_STAGES_ORDER = [
    "01_cut",
    "02_arrange",
    "03_audio",
    "04_effects",
]


def _find_work_dir() -> str:
    """从调用栈或环境变量找项目目录"""
    work_dir = os.environ.get("CLIPMIND_PIPELINE_DIR", "")
    if work_dir and os.path.exists(work_dir):
        return work_dir
    return os.getcwd()


def _stage_path(stage: str) -> str:
    work_dir = _find_work_dir()
    return os.path.join(work_dir, STAGE_DATA, stage)


def _input_path(stage: str) -> str:
    return os.path.join(_stage_path(stage), "input")


def _output_path(stage: str) -> str:
    return os.path.join(_stage_path(stage), "output")


def _pipeline_path() -> str:
    return os.path.join(_find_work_dir(), STAGE_DATA, "pipeline.json")


# ─── 工具函数 ──────────────────────────────────


@tool(
    name="init_stage",
    description="""初始化一个阶段目录,创建 input/output 子目录.

阶段名规则: 01_cut / 02_arrange / 03_audio / 04_effects

每个阶段有独立的 input(输入) 和 output(产出) 目录.
分身只读自己阶段的 input,只写自己阶段的 output,不跨阶段.
""",
    phase="all",
    category="pipeline",
    group="阶段数据",
    tags=["stage", "init", "pipeline"],
)
def init_stage(stage: str) -> str:
    """初始化一个阶段目录(创建 input/output 子目录).

    Args:
        stage: 阶段名,如 "01_cut"

    Returns:
        初始化结果信息
    """
    for d in [_input_path(stage), _output_path(stage)]:
        os.makedirs(d, exist_ok=True)
    status_path = os.path.join(_stage_path(stage), "status.json")
    if not os.path.exists(status_path):
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump({"stage": stage, "status": "ready", "completed": False}, f, ensure_ascii=False, indent=2)
    return f"✅ 阶段 {stage} 已初始化"


@tool(
    name="reset_stage",
    description="""清空某个阶段的所有产出(output),准备重做.
重做后下游阶段因为 input 来源变更,也需要重新执行.
""",
    phase="all",
    category="pipeline",
    group="阶段数据",
    tags=["stage", "reset", "pipeline"],
)
def reset_stage(stage: str) -> str:
    """清空某个阶段的所有产出(output),准备重做.

    Args:
        stage: 阶段名,如 "01_cut"

    Returns:
        重置结果
    """
    out = _output_path(stage)
    if os.path.exists(out):
        for f in os.listdir(out):
            os.remove(os.path.join(out, f))
    status_path = os.path.join(_stage_path(stage), "status.json")
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump({"stage": stage, "status": "reset", "completed": False}, f, ensure_ascii=False, indent=2)
    return f"🔄 阶段 {stage} 已重置,output 已清空"


@tool(
    name="list_stage",
    description="""列出管线中某个阶段的 input 或 output 文件.
不传 stage 参数则显示所有阶段的概览.
分身启动后先用这个看看当前阶段有什么数据.
""",
    phase="all",
    category="pipeline",
    group="阶段数据",
    tags=["stage", "list", "pipeline"],
)
def list_stage(stage: str = "", side: str = "input") -> str:
    """列出指定阶段的文件.

    Args:
        stage: 阶段名,为空时列出所有阶段
        side: "input" 或 "output"

    Returns:
        文件列表文本
    """
    if not stage:
        base = os.path.join(_find_work_dir(), STAGE_DATA)
        if not os.path.exists(base):
            return "(阶段数据目录不存在)"
        stages = sorted(os.listdir(base))
        stages = [s for s in stages if os.path.isdir(os.path.join(base, s)) and s[0].isdigit()]
        lines = ["## 管线阶段总览", ""]
        for s in stages:
            sp = os.path.join(base, s)
            inp = os.path.join(sp, "input")
            out = os.path.join(sp, "output")
            inp_files = [f for f in os.listdir(inp) if f.endswith(".json")] if os.path.exists(inp) else []
            out_files = [f for f in os.listdir(out) if f.endswith(".json")] if os.path.exists(out) else []
            status = "?"
            status_path = os.path.join(sp, "status.json")
            if os.path.exists(status_path):
                try:
                    with open(status_path, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    status = "✅" if st.get("completed") else "⏳"
                except Exception:
                    pass
            lines.append(f"  [{status} {s}] input: {len(inp_files)}个文件, output: {len(out_files)}个文件")
        return "\n".join(lines)

    target = _input_path(stage) if side == "input" else _output_path(stage)
    if not os.path.exists(target):
        return f"(阶段 {stage} 的 {side} 目录不存在)"
    files = [f for f in os.listdir(target) if f.endswith(".json")]
    if not files:
        return f"(阶段 {stage}/{side} 为空)"
    lines = [f"## 阶段 {stage}/{side} ({len(files)} 个文件)", ""]
    for f in sorted(files):
        fpath = os.path.join(target, f)
        size = os.path.getsize(fpath)
        lines.append(f"  {f} ({size/1024:.0f}KB)")
    return "\n".join(lines)


@tool(
    name="read_stage",
    description="""读取指定阶段的 JSON 文件.
分身启动后,用这个读取当前阶段的输入数据.
先 list_stage 看有哪些文件,再 read_stage 读具体内容.

side 参数: "input" 读输入(默认), "output" 读产出.
分身通常读 input(任务写在 input 里),必要时也可读 output(上游产出).
""",
    phase="all",
    category="pipeline",
    group="阶段数据",
    tags=["stage", "read", "pipeline"],
)
def read_stage(stage: str, filename: str, side: str = "input") -> str:
    """读取指定阶段 input 或 output 目录中的文件内容.

    Args:
        stage: 阶段名
        filename: 文件名
        side: "input" 或 "output", 默认 "input"

    Returns:
        文件内容(JSON 格式化)
    """
    if side == "output":
        fpath = os.path.join(_output_path(stage), filename)
    else:
        fpath = os.path.join(_input_path(stage), filename)
    if not os.path.exists(fpath):
        # 兼容: 如果 side 指定的目录没有,也看看另一边
        alt = os.path.join(_output_path(stage), filename) if side == "input" else os.path.join(_input_path(stage), filename)
        if os.path.exists(alt):
            fpath = alt
        else:
            return f"[错误] 文件不存在: {filename}(side={side})\n可用文件:\n" + list_stage(stage, side)
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        if len(content) > 10000:
            content = content[:10000] + f"\n\n...(截断,完整文件 {len(content)} 字符)"
        return content
    except Exception as e:
        return f"读取失败: {e}"


@tool(
    name="write_stage",
    description="""写入数据到指定阶段的 input 或 output 目录.
文件会自动保存为 JSON 格式(自动补 .json 后缀).

用法区别:
  - Director(你)写任务给分身 → side="input" (分身会 read_stage 读到)
  - 分身写自己的产出 → side="output" (Director 会 copy_to_stage 搬到下一阶段)

默认 side="output" (兼容分身使用).
""",
    phase="all",
    category="pipeline",
    group="阶段数据",
    tags=["stage", "write", "pipeline"],
)
def write_stage(stage: str, filename: str, content: str, side: str = "output") -> str:
    """写入数据到指定阶段的 input 或 output 目录.

    Args:
        stage: 阶段名
        filename: 文件名 (建议用 role名.json,如 cutter_a.json)
        content: JSON 内容字符串
        side: "input" 或 "output", 默认 "output"

    Returns:
        保存结果
    """
    target = _input_path(stage) if side == "input" else _output_path(stage)
    os.makedirs(target, exist_ok=True)
    if not filename.endswith(".json"):
        filename += ".json"
    fpath = os.path.join(target, filename)
    # 校验 JSON
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError as e:
        return f"[错误] content 不是有效 JSON: {e}"
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return f"✅ 已保存 → {STAGE_DATA}/{stage}/{side}/{filename} ({os.path.getsize(fpath)/1024:.0f}KB)"


@tool(
    name="mark_stage_done",
    description="""标记当前阶段为完成状态.
分身完成所有工作后调用,写上完成摘要.
Director 看所有上游阶段都标记完成,才进行下一步.
""",
    phase="all",
    category="pipeline",
    group="阶段数据",
    tags=["stage", "done", "pipeline"],
)
def mark_stage_done(stage: str, summary: str = "") -> str:
    """标记阶段为完成.

    Args:
        stage: 阶段名
        summary: 完成摘要

    Returns:
        标记结果
    """
    status_path = os.path.join(_stage_path(stage), "status.json")
    os.makedirs(os.path.dirname(status_path), exist_ok=True)
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump({
            "stage": stage,
            "status": "completed",
            "completed": True,
            "summary": summary,
        }, f, ensure_ascii=False, indent=2)
    return f"✅ 阶段 {stage} 标记完成"


@tool(
    name="get_stage_status",
    description="""查看整条管线的当前进度:每个阶段是否完成、input/output 文件数.
Director 用这个决定要不要进入下一阶段.
""",
    phase="all",
    category="pipeline",
    group="阶段数据",
    tags=["stage", "status", "pipeline"],
)
def get_stage_status() -> str:
    """查看整条管线的当前进度.

    Returns:
        管线状态文本
    """
    base = os.path.join(_find_work_dir(), STAGE_DATA)
    if not os.path.exists(base):
        return "(管线尚未初始化)"
    lines = ["## 管线进度", ""]
    for stage in _STAGES_ORDER:
        sp = os.path.join(base, stage)
        if not os.path.exists(sp):
            lines.append(f"  ⬜ {stage} — 未开始")
            continue
        inp = os.path.join(sp, "input")
        out = os.path.join(sp, "output")
        inp_files = [f for f in os.listdir(inp) if f.endswith(".json")] if os.path.exists(inp) else []
        out_files = [f for f in os.listdir(out) if f.endswith(".json")] if os.path.exists(out) else []
        status_path = os.path.join(sp, "status.json")
        status_icon = "⬜"
        if os.path.exists(status_path):
            try:
                with open(status_path, "r", encoding="utf-8") as f:
                    st = json.load(f)
                if st.get("completed"):
                    status_icon = "✅"
                elif st.get("status") == "running":
                    status_icon = "🔄"
                else:
                    status_icon = "⏳"
            except Exception:
                pass
        lines.append(f"  {status_icon} {stage} — input: {len(inp_files)}文件, output: {len(out_files)}文件")
    return "\n".join(lines)


@tool(
    name="copy_to_stage",
    description="""把上游阶段的产出复制到下游阶段的 input.
Director 在每个阶段完成后调用,把上一阶段的产出搬运到下一阶段的 input.

分身不用这个工具,分身只用 read_stage/write_stage.
""",
    phase="all",
    category="pipeline",
    group="阶段数据",
    tags=["stage", "copy", "pipeline"],
)
def copy_to_stage(from_stage: str, from_file: str, to_stage: str, to_file: str = "") -> str:
    """把上游阶段的产出复制到下游阶段的 input.

    Director 在每个阶段完成后调用,把上一阶段的产出搬运到下一阶段.

    Args:
        from_stage: 源阶段名
        from_file: 源文件名(在源阶段的 output 里)
        to_stage: 目标阶段名
        to_file: 目标文件名(在目标阶段的 input),为空则同名

    Returns:
        搬运结果
    """
    src = os.path.join(_output_path(from_stage), from_file)
    if not os.path.exists(src):
        return f"[错误] 源文件不存在: {STAGE_DATA}/{from_stage}/output/{from_file}"
    dst_name = to_file or from_file
    dst = os.path.join(_input_path(to_stage), dst_name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return f"✅ 已搬运: {from_stage}/output/{from_file} → {to_stage}/input/{dst_name}"
    except Exception as e:
        return f"[错误] 搬运失败: {e}"
