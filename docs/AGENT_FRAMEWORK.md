# Agent 框架精要

> 基于 Codex、OpenMontage、UniVA、Crayotter、Tesslate 等项目的架构研究。
> 2026-05-26

## 一、Agent Loop 核心模式

```
while True:
    response = llm(messages=messages, tools=tool_schemas)  ← tools 是 API 独立参数
    if not response.tool_calls: break                       ← 没工具调用 = 完成
    for call in response.tool_calls:                        ← 模型返回结构化调用
        result = execute(call.name, call.arguments)
        result = reserialize(result)                        ← 压缩结果
        messages.append(tool_result)
```

**关键：** tools 不是 prompt 的一部分，而是 API 的独立参数。模型在训练阶段就把 JSON Schema 工具定义内化了。

## 二、工具注册与发现

### 当前架构（Registry）

```
@tool(name="detect_scenes", phase="analyze", category="scene")
def detect_scenes(video_path: str, ...):
    """检测所有镜头切换点。不分割视频。"""
    ...

# 按阶段获取工具：
analyze_tools = get_tools_by_phase("analyze")
edit_tools = get_tools_by_phase("edit")
```

### 替代旧模式
```
TOOLS = [ToolDef(name=..., fn=...), ...]  ← ❌ 废弃
```

## 三、工具描述怎么写（决定 90% 的准确率）

每个工具的 description 必须包含以下结构：

```
1. 一句话说清楚做什么
2. 什么时候用（正面条件）
3. 什么时候不要用（负面条件 — 防止选错工具的最重要因素！）
4. 副作用（写文件？改数据库？耗时长？）
```

**模板：**
```
"做 X。当用户要求 Y 时使用。"
"不做 Z。需要 Z 请用 other_tool。"
"[副作用] 会在磁盘上创建文件。"
```

### 参数描述规则
- 用 enum 替代自由文本（参数错误几乎归零）
- 写合法范围（0~1、1~100 等）
- 写默认值

## 四、阶段式工具暴露

**问题：** 同一 prompt 超过 10-15 个工具时，模型选工具准确率显著下降。

**解法：** 按阶段分组暴露

| 阶段 | 暴露工具 | 任务 |
|---|---|---|
| analyze | scene、analyze | 分析素材，场景分割 |
| plan | arrange、track | 编排时间线，规划剪辑 |
| edit | colors、effects、audio、animation、timeline、mask、face | 创意编辑 |
| render | render | 渲染输出 |

**实现：** `get_tools_by_phase("analyze")` 只返回分析阶段的工具。

## 五、结果再序列化（Token 节省 40%+）

工具返回的原始 JSON 直接塞回上下文会浪费大量 token。

**规则：**
- 短结果（<200 字符）：原样
- JSON 列表：只保留前 3 项 + 总数
- 大 JSON：只保留关键字段
- 文件路径：只保留文件名

## 六、上下文压缩

当上下文达到窗口上限（~60000 token）时自动压缩：
- 保留 system prompt
- 保留最近 N 轮完整对话
- 中间部分压缩为文本摘要
- 保留工具调用序列信息

## 七、Agent to Agent 通信模式（未来参考）

### Plan/Act 分离（UniVA、Crayotter）

| 角色 | 职责 | 工具权限 |
|---|---|---|
| **Planner** | 理解用户意图，分解步骤 | 无工具调用 |
| **Executor** | 按计划选工具，填参数，执行 | 有工具调用 |

### Sub-agent 模式（Codex）

主 agent 可以 spawn 子 agent 并行处理独立子任务。每个子 agent 有独立的上下文窗口。

### 适用场景
- 复杂任务 > 5 步：用 Plan/Act 分离
- 并行独立子任务：用 Sub-agent

## 八、Skill 层（未来参考——OpenMontage 三层架构）

| 层 | 内容 | 模型读什么 |
|---|---|---|
| Layer 1: `tools/` + `registry.py` | "有什么" — 可执行能力 | tool schema |
| Layer 2: `skills/` | "怎么用" — 场景、参数策略、质量门 | Markdown 文件 |
| Layer 3: vendor knowledge | "底层原理" — API 参数调优 | Markdown 文件 |

**原则：** Python = 工具 + 持久化。不包含业务流程。所有编排逻辑和创意决策在 MD 文件里。
