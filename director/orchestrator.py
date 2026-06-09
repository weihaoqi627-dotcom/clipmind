"""
管线编排器 — 确定性代码执行 Director 写的计划书.

架构:
  Director (LLM) → 写 plan.json (定义阶段/角色/依赖)
  Orchestrator (代码) → 读 plan.json → 按阶段机械推进
  分身 (LLM) → 读 input → 执行 → 写 output

关键原则:
  - 管线结构由 Director 在 plan.json 中定义,不定死规则
  - Orchestrator 只做机械操作: init/写input/派分身/搬运/重试
  - 分身失败自动重试(最多2次),全部失败跳过继续
"""

import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from director.executor import run_executor
from director.tools.stage_storage import (
    init_stage, write_stage, read_stage, list_stage,
    mark_stage_done, get_stage_status, copy_to_stage,
)
from director.logging_config import get_logger

log = get_logger("director.orchestrator")


class Orchestrator:
    """管线编排器 — 确定性执行 Director 写的计划书"""

    MAX_RETRIES = 2

    def __init__(self, pipeline, work_dir: str):
        self.pipeline = pipeline
        self.work_dir = work_dir
        self.results = {}  # stage_name → {role_name → result}

    def execute(self, plan_path: str) -> dict:
        """执行计划书,逐阶段推进.

        Args:
            plan_path: plan.json 的完整路径

        Returns:
            {阶段名: {状态, 角色结果列表}}
        """
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)

        stages = plan.get("stages", [])
        if not stages:
            log.warning("计划书中没有定义阶段(stages)")
            return {"error": "no stages defined"}

        log.info("📋 管线启动: %d 个阶段", len(stages))
        for s in stages:
            roles = s.get("roles", [])
            parallel = s.get("parallel", False)
            mode = "并行" if parallel else "串行"
            log.info("   %s [%s] %s个角色", s["stage"], mode, len(roles))

        summary = {"stages": {}}

        for idx, stage_def in enumerate(stages):
            stage_name = stage_def["stage"]
            roles = stage_def.get("roles", [])
            is_parallel = stage_def.get("parallel", False)

            print(f"\n{'='*50}")
            print(f"  ▶ 阶段 {idx+1}/{len(stages)}: {stage_name}")
            print(f"{'='*50}")

            # ── 1. 初始化阶段目录 ──
            init_stage(stage_name)

            # ── 2. 准备 input (把任务写进 stage input) ──
            for role in roles:
                role_name = role.get("role_name", role.get("name", "unknown"))
                mission_text = role.get("mission", role.get("description", ""))
                tool_groups = role.get("tool_groups", "")
                done_when = role.get("done_when", "")

                # 在 mission 开头加注释,防止分身被 Director 写的 "去 read_stage(xxx)" 误导
                clean_mission = mission_text
                if "read_stage" in mission_text:
                    clean_mission = (
                        "[注意] 本文件就是你需要的任务描述,直接看下面的任务干活,"
                        "不要再去 read_stage 找别的文件。你的工具和数据都在这里了。\n\n"
                        + mission_text
                    )
                mission_doc = {
                    "role": role_name,
                    "mission": clean_mission,
                    "tool_groups": tool_groups,
                    "done_when": done_when,
                }
                filename = f"mission_{role_name}.json"
                write_stage(stage_name, filename,
                            json.dumps(mission_doc, ensure_ascii=False, indent=2),
                            side="input")
                log.info("  📝 写入任务 → %s/input/%s", stage_name, filename)

            # ── 3. 派发分身 ──
            stage_result = self._dispatch_roles(roles, stage_name, is_parallel)

            # ── 4. 自动捕获产出(分身可能忘记写 output 文件) ──
            any_success = any(
                r.get("completed") for r in stage_result.values()
            )
            # 如果分身超时但已经写了 output 文件,也算部分成功
            any_output = self._check_has_output(stage_name)
            if any_success or any_output:
                success_roles = [n for n, r in stage_result.items() if r.get("completed")]
                if not success_roles and any_output:
                    success_roles = ["(分身超时但已产出文件)"]
                if any_success:
                    self._auto_capture_output(stage_name, roles)
                mark_stage_done(stage_name, f"完成角色: {', '.join(success_roles)}")
                log.info("  ✅ 阶段 %s 完成", stage_name)
            else:
                log.warning("  ⚠️ 阶段 %s 所有分身均失败,跳过", stage_name)

            summary["stages"][stage_name] = {
                "status": "completed" if any_success else "skipped",
                "roles": stage_result,
            }

            # ── 5. 搬运输出到下一阶段 input ──
            if idx + 1 < len(stages) and any_success:
                next_stage = stages[idx + 1]["stage"]
                self._propagate_outputs(stage_name, roles, next_stage)

        # ── 最终状态 ──
        print(f"\n{'='*50}")
        print("  📊 管线完成")
        print(f"{'='*50}")
        status = get_stage_status()
        print(status)

        return summary

    # ── 内部方法 ─────────────────────────────────────

    def _dispatch_roles(self, roles: list, stage_name: str, parallel: bool) -> dict:
        """派发分身(并行或串行),带重试逻辑."""
        if parallel and len(roles) > 1:
            return self._dispatch_parallel(roles, stage_name)
        results = {}
        for role in roles:
            r = self._dispatch_single(role, stage_name)
            results[role.get("role_name", role.get("name", "?"))] = r
        return results

    def _dispatch_parallel(self, roles: list, stage_name: str) -> dict:
        """并行派发多个分身,全部完成后返回."""
        role_names = [r.get("role_name", r.get("name", f"role_{i}")) for i, r in enumerate(roles)]
        print(f"\n  🚀 并行派发 {len(roles)} 个分身: {', '.join(role_names)}")

        results = {}
        with ThreadPoolExecutor(max_workers=len(roles)) as pool:
            future_map = {
                pool.submit(self._run_with_retry, role, stage_name): role
                for role in roles
            }
            for future in as_completed(future_map):
                role = future_map[future]
                name = role.get("role_name", role.get("name", "?"))
                try:
                    results[name] = future.result()
                except Exception as e:
                    results[name] = {"completed": False, "error": str(e)[:300]}
                    log.error("  ❌ %s 异常: %s", name, e)

        for name, r in results.items():
            icon = "✅" if r.get("completed") else "❌"
            turns = r.get("turns", 0)
            elapsed = r.get("elapsed", 0)
            print(f"  [{icon} {name}] {turns}turns, {elapsed:.0f}s")
            if not r.get("completed") and r.get("error"):
                print(f"    ⚠ {r['error'][:200]}")

        return results

    def _dispatch_single(self, role: dict, stage_name: str) -> dict:
        """串行派发单个分身."""
        name = role.get("role_name", role.get("name", "?"))
        print(f"\n  🚀 派发: {name}")
        result = self._run_with_retry(role, stage_name)
        icon = "✅" if result.get("completed") else "❌"
        print(f"  [{icon} {name}] 完成")
        return result

    def _run_with_retry(self, role: dict, stage_name: str) -> dict:
        """运行分身(带重试)."""
        mission = role.get("mission", role.get("description", ""))
        tool_groups_str = role.get("tool_groups", "")
        done_when = role.get("done_when", "")

        # 解析 tool_groups (可以是字符串或列表)
        if isinstance(tool_groups_str, str):
            tool_groups = [g.strip() for g in tool_groups_str.split(",") if g.strip()]
        else:
            tool_groups = tool_groups_str or []

        for attempt in range(self.MAX_RETRIES + 1):
            if attempt > 0:
                log.info("  🔄 重试第 %d 次...", attempt)
                # 清理 output,防止旧产出干扰重试判断
                out_dir = os.path.join(self.work_dir, "stage_data", stage_name, "output")
                if os.path.exists(out_dir):
                    import shutil
                    shutil.rmtree(out_dir, ignore_errors=True)
                    os.makedirs(out_dir, exist_ok=True)

            result = run_executor(
                agent_type="分身",
                mission=mission,
                params={},
                pipeline=self.pipeline,
                tool_groups=tool_groups or None,
                done_when=done_when,
                verbose=True,
            )

            if result.get("completed"):
                return result

            # 失败 — 记录日志然后重试或跳过
            error = result.get("error", "未知错误")
            log.warning("  ⚠ 分身失败(尝试 %d/%d): %s",
                        attempt + 1, self.MAX_RETRIES + 1, error[:200])

            if attempt < self.MAX_RETRIES:
                log.info("  🔄 即将重试...")

        # 所有重试耗尽
        return result  # 返回最后一次失败的结果

    def _check_has_output(self, stage_name: str) -> bool:
        """检查阶段的 output 目录是否已有文件(分身虽然超时但可能写了产出)."""
        out_dir = os.path.join(self.work_dir, "stage_data", stage_name, "output")
        if os.path.exists(out_dir):
            files = [f for f in os.listdir(out_dir) if f.endswith(".json")]
            return len(files) > 0
        return False

    def _auto_capture_output(self, stage_name: str, roles: list):
        """克隆完成后自动捕获产出:若 output 目录为空,从草稿状态自动生成."""
        out_dir = os.path.join(self.work_dir, "stage_data", stage_name, "output")
        if os.path.exists(out_dir):
            existing = [f for f in os.listdir(out_dir) if f.endswith(".json")]
            if existing:
                log.info("  📦 已有 %d 个产出文件,跳过自动捕获", len(existing))
                return

        # 尝试从草稿捕获当前状态
        try:
            from director.draft import show_draft
            draft_state = show_draft(draft_id="main")
            if draft_state and "错误" not in draft_state[:10]:
                import json
                capture = {
                    "auto_captured": True,
                    "stage": stage_name,
                    "draft_state": draft_state,
                    "roles": [r.get("role_name", r.get("name", "?")) for r in roles],
                }
                content = json.dumps(capture, ensure_ascii=False, indent=2)
                write_stage(stage_name, f"auto_capture_{stage_name}.json",
                            content, side="output")
                log.info("  📸 自动捕获产出: %s/output/auto_capture_%s.json",
                         stage_name, stage_name)
        except Exception as e:
            log.info("  (自动捕获跳过: %s)", e)

    def _propagate_outputs(self, from_stage: str, roles: list, to_stage: str):
        """把阶段的产出搬运到下一阶段 input."""
        # 先看 output 目录有什么文件
        out_dir = os.path.join(self.work_dir, "stage_data", from_stage, "output")
        if not os.path.exists(out_dir):
            log.info("  (无产出可搬运: %s/output 不存在)", from_stage)
            return

        files = [f for f in os.listdir(out_dir) if f.endswith(".json")]
        if not files:
            log.info("  (无产出可搬运: %s/output 为空)", from_stage)
            return

        # 检查目标阶段是否已初始化
        init_stage(to_stage)

        for fname in files:
            copy_to_stage(from_stage, fname, to_stage)
