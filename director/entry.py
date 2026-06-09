"""
ClipMind — 入口
====================
唯一的入口:启动 Director Agent.

用法:
    python -m director.entry demo.mp4
    python -m director.entry --cli
"""
import sys, json, os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# ─── 工具加载 ──────────────────────────────────────────────

def _load_all_tools():
    """导入各工具模块,触发 @tool 装饰器注册"""
    import director.tools.analyze       # noqa: F401
    import director.tools.arrange        # noqa: F401
    import director.tools.effects        # noqa: F401
    import director.tools.render         # noqa: F401
    import director.tools.colors         # noqa: F401
    import director.tools.mask           # noqa: F401
    import director.tools.audio          # noqa: F401
    import director.tools.stabilize      # noqa: F401
    import director.tools.animation      # noqa: F401
    import director.tools.timeline       # noqa: F401
    import director.tools.denoise        # noqa: F401
    import director.tools.scene          # noqa: F401
    import director.tools.track          # noqa: F401
    import director.tools.face           # noqa: F401
    import director.tools.preview        # noqa: F401
    import director.tools.presets        # noqa: F401
    import director.tools.transcript    # noqa: F401
    import director.tools.watch          # noqa: F401  ← AI 直接用 DashScope SDK 看视频
    import director.tools.cut            # noqa: F401  ← 多阶段管线裁剪工具
    import director.tools.review         # noqa: F401  ← Director 审查输出
    import director.tools.prospect       # noqa: F401  ← 画面/语音勘探
    import director.tools.audio_prospect # noqa: F401  ← 语音勘探(独立模块)
    import director.tools.hf_catalog     # noqa: F401  ← HF 模板目录系统
    import director.tools.gsap_catalog   # noqa: F401  ← GSAP 情绪映射 + 动画模板
    import director.draft                # noqa: F401
    import director.memory_store         # noqa: F401  ← search_memory / get_index_info


def init_registry():
    """初始化 Registry(导入 + 打印摘要)"""
    _load_all_tools()
    from director.registry import get_all_tools, get_phase_map
    tools = get_all_tools()
    phases = get_phase_map()
    print(f"  Registry 已加载 {len(tools)} 个工具")
    for p, names in phases.items():
        if names:
            print(f"    {p}: {', '.join(names[:8])}{'...' if len(names) > 8 else ''}")


# ─── Director 入口 ─────────────────────────────────────────

def start_director(
    video_paths: list[str],
    task: str = "",
    max_turns: int = 100,
    verbose: bool = True,
    on_event: callable = None,
    workflow: str = "",
) -> dict:
    """
    启动 Director Agent(推荐入口).

    Args:
        video_paths: 素材路径列表
        task: 用户补充描述(可选)
        max_turns: 最大步数
        verbose: 是否打印日志
        on_event: 事件回调
        workflow: 工作流名称(已废弃,保留兼容)

    Returns:
        执行结果字典
    """
    _load_all_tools()
    from server.director_runner import DirectorRunner

    events = []

    def cb(evt, data):
        events.append((evt, data))
        if on_event:
            on_event(evt, data)
        if verbose:
            if evt == "ai_message":
                print(f"[Director] {data.get('content', '')[:200]}")
            elif evt == "progress":
                print(f"[{data.get('status','')}] {data.get('stage','')}")

    runner = DirectorRunner(event_callback=cb)
    runner.start_project(video_paths, task)
    runner.start_pipeline()
    runner.wait(timeout=3600)

    return {
        "completed": True,
        "events": events,
    }


# ─── CLI ────────────────────────────────────────────────────

def cli():
    """交互式 CLI"""
    # 预热注册表
    print("加载工具注册表...")
    init_registry()
    print()

    print(f"{'='*60}")
    print("  ClipMind Director — AI 顶级剪辑师")
    print(f"{'='*60}\n")

    print("素材路径(每行一个,空行结束):")
    paths = []
    while True:
        p = input("  > ").strip().strip('"').strip("'")
        if not p:
            break
        if os.path.exists(p):
            paths.append(p)
        else:
            print(f"  ⚠ 文件不存在: {p}")

    if not paths:
        print("没有有效素材路径")
        return

    extra = input("\n补充描述(回车跳过): ").strip()
    max_turns = input("最大步数 [默认100]: ").strip()
    max_turns = int(max_turns) if max_turns.isdigit() else 100

    start_director(video_paths=paths, task=extra, max_turns=max_turns)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--cli", "-i"):
        cli()
    elif len(sys.argv) > 1:
        start_director(video_paths=sys.argv[1:])
    else:
        cli()
