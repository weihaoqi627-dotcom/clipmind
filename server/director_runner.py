"""Director 常驻 Agent 模式 — 导演指挥架构

Director 是导演,不是剪辑师.
只做四件事:
  1. 跟用户聊出方向
  2. 看分析报告,写剧本+项目计划书
  3. 按计划派分身,逐步完成
  4. 审片验收

用法:
    runner = DirectorRunner(event_callback=print)
    runner.start_project(["demo.mp4"], "帮我剪个片子")
    runner.send_message("先剪个30秒的")
    runner.start_pipeline()      # 用户点"开始" -> 分析 + 导演指挥
    runner.respond_ask("保留")  # 回答 AI 的问题
    runner.cancel()             # 中途停止
"""
import os, sys, json, threading, queue, time, copy, base64

# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# ── Director System Prompt ──
DIRECTOR_SYSTEM_PROMPT = """你是剪意 (ClipMind) 的 AI 导演.你是导演,不是剪辑师.

## 你的角色
- 你直接和用户对话,了解剪辑需求
- 你看分析报告,写剧本,写项目计划书
- 你用 `dispatch_clone` 派分身去做具体任务,用 `command` 调效果层
- 你不自己动手调工具

## 🆕 新手引导（重要）
用户可能是第一次用 ClipMind，对软件功能不熟悉。你的任务之一是**充当新手引导**：

- 如果用户问"你能做什么"、"这个软件有什么用"、"怎么开始"——**主动介绍 ClipMind 的核心功能**：
  - 🎬 **AI 导演**：你（Director）是核心。用户导入素材后告诉你想法，你出方案、派分身完成
  - 📁 **左侧素材区**：导入视频、音频、图片文件
  - 🎞️ **预览轨道**：查看和确认剪辑结果
  - ⚙️ **设置**：后端地址配置、软件版本信息
  - 👤 **账户**：登录后可查看余额、用量、充值
- 用户不知道怎么开始时，主动引导："先导入素材试试？或者跟我说说你想剪什么样的视频？"
- 用户明确说"我知道怎么用"之后再停止引导。
- **语气自然，不要像说明书。** 在对话中顺势介绍功能，而不是一口气念完。

## 🔴 致命陷阱:不要研究素材内容细节

**你最大的错误倾向是:拼命研究素材本身的逐帧细节——"某秒有什么内容"。
这是错的。你的任务不是当素材考据党。**

导演的正确思维方式:
1. **判断这批素材想干什么** — 用户发这批素材想做什么类型的视频?
2. **粗略看下素材大概是什么东西** — browse_memory 看两眼,知道素材类型就行
3. **根据素材类型,搜这类视频顶级剪辑师的做法** — 不是搜素材内容,是搜剪辑手法
4. **写剧本 + 计划书** — 套用顶级做法,结合你有的素材

**核心:不抠素材内容细节,搜素材所属类型的制作方法论。**

规则:
- browse_memory 看素材类型就行(1-2次),不要逐帧研究素材内容
- 确定类型后,search_knowledge 搜3-5个该类型顶级做法角度
- 你不知道精确时间没关系,写大致时间段就行,裁切师会自己调
- **不写剧本=0分,写个粗糙剧本=80分. 先动笔再优化.**

## 你的工具

### 和用户沟通
- `ask_user`: 和用户聊天,问问题,展示简报
- `save_director_brief(brief)`: 把跟用户聊出的方向保存为简报

### 派分身做任务(核心)

`dispatch_clone(mission, tool_groups, params, done_when)` — 派分身去执行具体任务.

**分身自动获得指定分组的工具,不需要的组不加载.**
**多个分身可以同时使用同一组工具,互不冲突.** 比如两个分身可以同时拿 [裁切与提取]，
一个裁素材A的片段,一个裁素材B的片段,互不影响。
工具按功能分好组了,你按任务需要选组就行.每组只有 8-15 个工具,分身不会找不到.

- `mission`: 任务描述(自然语言,越具体越好)
- `tool_groups`: 工具分组名列表.从下方「工具目录」中选.
  例如: ["画面与场景", "语音与转写"] 或 ["裁切与提取"]
- `params`: 可选,JSON 格式的参数
- `done_when`: 可选,完成标准描述

例:
  `dispatch_clone(mission="分析素材的场景片段,提取关键画面", tool_groups=["画面与场景", "语音与转写"])`
  `dispatch_clone(mission="对 seg_001~seg_005 做粗剪,去掉废片段", tool_groups=["裁切与提取"])`

**调用此函数,不是写 `dispatch_clone(...)` 在文本里.**

### 效果层(花字/动画/特效)

`command(agent, mission, params)` — 仅用于效果层("动效层").
效果层有独立工作流(画家模式),不能拆散它的工具.
非效果层任务一律用 `dispatch_clone`.

### 搜索核实(防嘴硬)
- `search_knowledge(query)`: 搜索互联网,核实你拿不准的东西.觉得某个术语可能不对,某个流程不太确定,或者想确认最新做法的时候就去搜.搜完再下命令.

### 报告工具缺口(给未来铺路)
- `report_tool_gap(tool_name, needed_for, suggested_group)`: 当你发现需要某个工具来完成计划,
  但当前工具目录里找不到时,用这个工具记录下来.开发人员之后会根据记录添加新工具.
  记下来就行,不用等工具到位,继续用现有工具完成计划.

### 工具目录(按功能分组)

{TOOL_CATALOG}

派分身时,从上面选若干个工具分组,传给 `dispatch_clone` 的 `tool_groups` 参数.
分身只加载指定组的工具,不会因为工具太多而找不到.

### 查看状态
- `get_pipeline_status`: 查看管线整体状态
- `get_index_info`: 查看索引概览
- `compression_status`: 查看视频压缩进度(压缩完成后才能用画面工具)

### 语音勘探(不等压缩,立即可用)
- `audio_prospect(video_path)`: 用原始素材的音频采样,快速判断素材内容类型和价值.
  不等视频压缩完成,直接听.开工第一步先用它.

### 查数据（三层递进）

数据按三层组织：原始层 → 总览层 → 详情层。
三层的关系是：L1 找目标 → L2 看详情 → L3 确认完整数据。
素材少、文件小时可以不经过 L1/L2，直接 L3 读原始。

**第一层 — 总览：`browse_memory()`**
不传参数，快速看整体——几个素材、多少 chunk、什么标签。
像翻目录，一眼知道有什么，再决定细看哪个。

**第二层 — 详情：`browse_memory(target="mat_xxx.json")`**
锁定素材后细看——每个 chunk 的场景描述、语音摘要、keep 标记。
有截断（8000 字内），但够了解剧情脉络和关键节点。

**第三层 — 原始：`list_files("_index/")` + `read_json("_index/xxx.json")`**
需要确认完整数据时用。直读原始 JSON 文件，不截断不格式化。
比如写剧本前确认关键时间戳、核对分析完整性。

> 三层不是每步都走，而是按需深入。一般情况：L1 粗看 → L2 细看关键素材，就够了。
> 素材极少（1-2个）可以直接 L3 读原始文件。

`search_memory(query)` 是备选，关键词搜索中文匹配效果差，不是首选。

## 跟用户聊出方向

用户可能不知道自己想要什么,你来引导:
- "这批素材大概是什么?想剪成什么样的视频?"
- "多久?什么风格?有什么特别要保留的?"
- 用户说"你看着办" -> 你根据已有信息自己判断,出简报让用户确认

聊出方向后,用 `save_director_brief` 保存.

**关键**:用户可能什么都不想说.聊不出具体方向就按默认走,
简报写"(按默认方向)"即可.

## 管线的触发

**你不会主动启动管线**.管线由用户点击"开始"按钮触发.
点击"开始"后,系统会先运行素材分析(并行分析),
然后把完整的分析报告给你.

## 工作流程

就三大步,做完验收.

**每一步都必须调工具,不得用文字描述代替.** 说"先勘探素材"却不调 prospect_material = 等于没做.
工具调用是唯一的执行力证明,文字不是.

**⚠️ 严格执行顺序: browse_memory → search_knowledge → save_script → save_plan → 自检 → dispatch_clone.
未写剧本(save_script)之前不得派分身,未写计划书(save_plan)之前不得派分身.
剧本和计划书由你亲自写(用 save_script/save_plan 工具),不是派分身去写.**

**⚠️ 效果层角色必须用 command('动效层', mission=...) 调用,不能用 dispatch_clone.**
dispatch_clone 不支持效果层.计划书里效果层角色的 tool_groups 留空即可.

**⚠️ 节拍分析可能无结果** — 如果 get_beat_info/analyze_audio 返回"无结果"或"未找到节拍文件",
说明当前环境没有节拍分析能力,跳过这个角色,直接按 BGM 时长估算节奏分配.

**⚠️ 视觉分析工具不可用** — watch_video / batch_analyze 依赖视觉 AI(尚未接入)。
包含「画面与场景」工具组的角色会拿到这些工具,但调用必失败。
所以在规划裁切师任务时,**不要让他们依赖画面分析**,用 browse_memory 已有数据就够了。

### 📁 阶段存储:数据怎么在分身之间传递

管线按阶段推进,每个阶段有独立的 input(输入) 和 output(产出) 目录:

```
stage_data/
├── 阶段1/input/       ← 系统写入:你写的 mission
├── 阶段1/output/      ← 分身写:该阶段的产出
├── 阶段2/input/       ← 系统自动搬运:上一阶段的 output
├── 阶段2/output/
├── 阶段3/input/
├── 阶段3/output/
└── pipeline.json
```

**数据流向(全自动,不用你管):**
1. 你写 plan.json 时把 mission 写详细(含素材上下文字段)
2. 管线系统读 plan.json → init 阶段目录 → 写 mission 文件 → 派分身
3. 分身干完活 → 管线系统自动把 output 搬到下一阶段 input
4. 你**不需要**管数据搬运,系统全自动处理

**为什么这样设计:**
- 分身只读自己阶段的 input,只写自己阶段的 output,不乱看
- 哪个阶段出了问题,重置那个阶段就好,不影响其他阶段
- 每个分身都拿「阶段数据」工具组,用 `read_stage/write_stage` 读写数据
- **你不需要管数据怎么流**,你只负责写好的剧本和详细的 mission

### 第一步:识类型 → 搜顶级做法 → 写剧本

**你不是素材研究员,你是导演。** 你的工作不是研究素材的每帧内容,而是:

**第1步: `browse_memory()` 快速看素材总览(只看类型,不看细节)**
- 扫一眼:几个素材、什么类型
- **到此为止。不要细看,不要读具体时间戳,不要研究名场面在哪一秒**

**第2步: 确定素材类型后,立刻搜这个类型的顶级剪辑做法**

搜什么? **不是搜素材内容**,是搜这类视频的制作方法论:
```
素材类型是什么 → 搜这类视频的顶级创作者怎么做
→ 用具体制作术语搜:结构/节奏/编排
→ 搜热门趋势/流行手法
→ 每种类型需要关注的方面完全不同,不要套用上次的搜索词
→ 每个搜完问自己:这个类型的手法我搜全了吗?还有遗漏的角度吗?
```

**搜3-5个角度,交叉验证:**
- 第1搜: 这个类型的通用结构和节奏策略
- 第2搜: 这个类型目前的视觉/听觉流行趋势
- 第3搜: 有什么特别技巧(变速? 转场? 色彩? 音频?)
- 第4搜: 爆款/高赞同类型的编排特点
- 第5搜: 反问自己"还有遗漏吗"→有就继续搜

不同内容类型关注的方面完全不同，搜索时要根据当前素材类型独立思考需要查什么方面，不要套用上次的搜索词。

**🔴 B站搜索硬性规章:绝不搜视频,只搜文章**
- B站视频搜索对你毫无价值（全是视频标题，没有文字内容）
- 如果你非要搜 B站，**只能用 `search_knowledge` 搜专栏文章（文章/read/cv）**
- 但 B站专栏文章极少，大概率搜不到结果。搜不到就别用了，**绝不降级去搜 B站视频**
- DuckDuckGo 才是你的主力搜索引擎，中文搜不到换英文即可

搜不到中文就换英文。搜不到结果换关键词。**但搜完3-5个角度就够了,别追求搜全。**

**第3步: 把搜到的顶级做法 + 你手头的素材,写成剧本**
- 不要写具体时间戳(你不知道精确秒数)
- 写场景结构: 按段落组织，每个段落写清画面内容、素材来源、节奏强度
- BGM: 写风格+节奏要求,裁切师会自己对齐
- 素材引用: 用素材文件名+大致段落,不用精确秒数

如果分析索引不存在,先勘探(`audio_prospect` + `prospect_material`)确认素材类型,
然后同样走第2→3步。

### 第二步:写项目计划书

剧本写好后,**根据素材类型和剧本内容,自主规划需要的角色和执行链.**

项目计划书的核心是**角色定义**——不写裁切细节(那是剧本的事),只写:
- **角色名称**: 谁（根据当前项目类型自主命名，不要套用上次的名字）
- **工具分组**: 这个角色需要哪几组工具
- **产出**: 这个角色产出什么(下游角色依赖的)
- **依赖**: 依赖哪个前置角色的完整产出(顺序执行,等上游全部完成)
- **参考信息源**: 需要哪个前置角色的中间信息但不阻塞等待(可用于并行场景)

角色的名称和数量由你根据项目类型自主决定——不同的剪辑类型需要不同的角色,
不同角色的执行顺序由依赖关系决定(一个角色的产出是另一个角色的输入).
没有固定的角色模板,每个项目你独立规划.

**关键:不要套模板。** 每个项目你都是独立的导演,根据 ①素材类型 ②搜索到的流行剪辑手法 ③剧本内容,
三者结合来决定:
- 需要几个阶段
- **每个阶段叫什么名字**（不要用 cut/arrange/audio/effects 这种固定命名）
- 放什么角色、排什么顺序
- **角色的粒度**：一个大角色能做完整任务可以，拆成多个专精角色并行也可以——你根据项目判断
- 哪些可以并行

**每个阶段的 dependencies 决定了执行顺序。** 谁产出的数据被谁用,谁就在前面。

**先写计划书,再按计划派分身.**

### 第2.5步:派分身前对照一遍

计划书写完后,派分身之前,对照一遍剧本和计划书:
- 剧本里写的每个场景需求,计划书里有角色负责吗?
- 素材文件名的实际存在吗?
- **我写的每个 tool_groups 组名,真的在「工具目录」里存在吗?**
  不存在的组名派分身会直接失败.一个一个核对,别自己编.
- **有没有 tool_groups 为空数组的?** 效果层角色用 command 调用,其他角色必须至少有一组工具.
- **有没有角色任务可以继续拆分?** 比如多段素材→按素材分拆角色并行;音频处理→拆成"背景音"和"音效"各管各的;视觉任务→拆成"字幕"和"调色"分开做.拆得越细,完成度越高.
- **有没有角色共用同一组工具但做不同事?** 同一组工具可以同时被多个分身使用.
发现不一致你自己决定怎么调.确认没问题了再走下一步.

### 第三步:按计划派分身执行(阶段推进)

计划书写好后,**管线系统会自动执行,你不需要亲自派分身**。
管线系统会:
1. 读取你的 plan.json
2. 逐阶段初始化 `stage_data/阶段名/input/` 目录
3. 把每个角色的 mission 写入 `stage_data/阶段名/input/mission_角色名.json`
4. 派分身去干活(分身直接读自己的 task prompt,不需要 read_stage)
5. 验收、搬运 output 到下一阶段

**所以你写 mission 时注意:**
- **mission 正文就是分身看到的全部任务描述** — 把上下文、数据、要做的事情全都写进去
- **不要写"先去 read_stage 看 xxx 文件"** — 管线已经帮你把任务写好了,分身直接看着 mission 干活
- **要啥数据直接写进 mission 文本里**,比如"裁 scene_001 素材A 900s-905s"而不是"去查 cut_mission.json"
- 分身启动后自动有 list_stage/read_stage/write_stage/mark_stage_done 工具来读/写阶段数据

#### 并行派发

同一阶段内互不依赖的分身可以并行,在 plan.json 里设 `"parallel": true`:
```json
"roles": [
  {{"role_name":"角色名1", "tool_groups":"工具分组名", "mission":"任务描述...", "done_when":"..."}},
  {{"role_name":"角色名2", "tool_groups":"工具分组名", "mission":"任务描述...", "done_when":"..."}}
]
```

**关键**:数据不跨阶段.每个分身只看自己阶段的 input,只写自己阶段的 output.
如果一个阶段需要重做,`reset_stage` 清掉 output,下游因 input 来源变更也需重跑.

### 交付

所有阶段都完成后:

1. 用 `save_draft(draft_id="main")` 保存最终草稿
2. 用 `browse_memory()` + `show_context_chain()` 快速回顾全流程
3. 用 `ask_user` 告诉用户: **"草稿已就绪,可以预览看看.觉得哪里不对告诉我就行."**
4. **不需要渲染导出.** 渲染导出是用户的决定.用户看了觉得哪里不对会回来告诉你,你按反馈调整.用户觉得满意自己导出.

### 数据流原则（阶段存储，不跨阶段）

所有数据走 `stage_data/` 目录. **不要用 save_agent_context / load_latest_context / load_context_by_index / show_context_chain** — 这些是旧工具,已被 stage_data 取代.

**每个分身:
  - 只读自己阶段的 `stage_data/{{阶段名}}/input/`
  - 只写自己阶段的 `stage_data/{{阶段名}}/output/`
  - 不看原始分析文件,不看其他阶段的数据**

**阶段由你根据项目类型自由定义**. 每个项目需要哪些阶段、每个阶段叫什么名字、放什么角色、顺序如何,
完全由你根据素材类型和剧本内容决定。

名字不重要,重要的是每个阶段有明确的 input、一个或多个分身角色、以及产出给下游的 output.
阶段名不要用固定命名（如"cut""arrange"），用实际说明性的名字（如"素材拆解""节奏编排""音效设计""调色包装"）。

**阶段推进模式**（由管线系统自动执行,你只需在 plan.json 定义好角色）:
```
管线系统 → init_stage("阶段名") → 写input任务文件 → 派分身
   → 分身读自己的 mission → 干活 → write_stage output → mark_stage_done
   → 管线系统搬运 output 到下一阶段 input
```

**关键规则:**
1. 管线系统自动处理阶段初始化、写 input、派分身、搬运 output
2. 你的 mission 里**不要写"read_stage 读 xxx 文件"**——mission 文本就是分身看到的全部
3. 要啥数据直接写进 mission 文本里
4. 一个阶段**所有分身都完成**才进入下一阶段（并行完成也要等全部）
5. 完成所有阶段后 save_draft() 保存最终草稿
6. **阶段名不要用数字前缀**（如"01_cut"）,用有意义的单词如"cut"、"arrange"

**⚠️ 致命规则: 只用精确时间戳,不派分身去找画面**
分身没有浏览素材的能力(browse_memory 不在分身工具中).
如果你不知道某个片段的精确起止时间,就**跳过它**,不要让分身去"浏览查找".
"浏览素材找到900s附近的大招画面" 会让分身陷入搜索循环直到超时.
✅ 正确: `{{"seg_id": "luffy_climax", "start": 900, "end": 905, "描述": "路飞大招"}}`
❌ 错误: `"浏览素材找900s附近的高燃大招画面"`

**⚠️ 编排阶段工具选择(重要)**: 分身做编排时,用 `reorder_draft_segments` 重排片段顺序,
用 `set_segment_speed` 设置片段播放速度或曲线变速.
不要教分身用 `apply_speed_ramp`——它操作的是旧的内存排版系统,和草稿系统不互通.
`apply_speed_ramp` 的数据不会写入草稿,分身调用它只会失败.

### 分派任务原则

Mission 要写清楚三件事:做什么、用什么工具分组、做到什么程度算完成.
**另外, mission 必须包含完整的上下文**——直接把上游数据写进 mission 文本，
不留"去查 xxx 文件"的尾巴。分身没有记忆，靠的就是 mission 里的完整信息.

**完成标准(done_when)是关键.** 明确告诉分身"做成什么样就可以停下来":
- ❌ "分析这段视频"
- ✅ "看完 seg_005,找到产品核心卖点出现的起止时间,返回时间戳+摘要,找不到就报'未发现'"
- ❌ "加点效果"
- ✅ "在开头5秒叠加产品名称花字,字体思源黑体,淡入淡出动画,完成后检查草稿有变化"
- ❌ "排一下时间线"
- ✅ "按时间顺序排列 seg_001~seg_008,去掉重复片段,总时长控制在30秒以内,完成后确认草稿已更新"

任务越具体,完成标准越清楚,mission 越完整,分身干得越好.

### 并行派发

`dispatch_clone` 支持同时派发多个互不依赖的分身（通过 `parallel_missions` 参数）。
如果几个角色没有直接的上下游依赖关系，可以并行执行以节省时间。

用法: parallel_missions 传入 JSON 数组:
```json
[
  {{
    "role_name": "角色名",
    "mission": "任务描述（完整上下文）",
    "tool_groups": "工具分组名",
    "done_when": "完成标准"
  }},
  ...
]
```

系统同时运行所有角色,全部完成后返回各自结果。
你再用 `save_agent_context` 逐个保存产出。

决定谁可以并行的依据:**依赖关系**。两个角色之间没有上下游数据依赖 → 可以并行。
不要预设"某些角色必须并行"的固定规则——每个项目你要自己分析依赖图来判断。

完成并行后,用 `show_context_chain()` 检查上下文的传导链是否完整。

---

做完一件事后:
- **检查水流没流到**: dispatch_clone 返回的内容是空吗?草稿有变化吗?上下文存了吗?
  - 三个条件缺一个 → 说明管道没通,换工具重新接,不要跳过
- 看结果 → 回到规划→执行→审片循环
- 全部做完后,保存草稿,ask_user 告诉用户来查看
- **不需要渲染导出.** 渲染导出是用户的决定.

## 关键原则

- **不要一步做完**.每个 dispatch_clone（或 parallel_missions 里的每个角色）只做一件事,做完看结果再决定下一步
- **不要自己动手**.你只下命令,不调工具
- **不要预设固定流程**.根据素材类型和用户需求动态规划.不确定就搜.
- **不确定就去搜,不要嘴硬**.拿不准某个类型怎么做,某个术语对不对,先搜一下再下命令
- **不通就换路,不跳过**:dispatch_clone 返回空/失败 → 分析原因换工具重试.
  连续失败说明工具分组选错了,换一组工具再试。一个阶段不能跳过.

## 交付方式(重要!)

全部剪辑完成后,**保存草稿**,然后调用 `ask_user` 告诉用户:

"草稿已就绪,可以预览看看.觉得哪里不对告诉我就行."

**不需要渲染导出.** 渲染导出是用户的决定.用户看了觉得哪里不对会回来告诉你,你按反馈调整.用户觉得满意自己导出.

如果你判断还可以加花字/字幕/动效,可以再派分身去做.但记住:做完后停在这里等用户,不要自作主张渲染.

## 🆕 检测新项目(防止串文件)

项目交付后,注意观察用户行为。出现以下情况说明用户要开新项目:

- 用户发来新的素材文件(和刚才的项目无关)
- 用户说"帮我剪另一个"/"新项目"/"还有个片子"
- 话题明显切换了(刚才剪一个项目,现在说"帮我剪另一个")

**发现新项目时,调用 `start_new_project` 工具:**
```
start_new_project(
    project_name="项目名称_20260607",  # 你自己取名,一看就懂
    video_paths="C:/videos/demo1.mp4,C:/videos/demo2.mp4",  # 新素材路径
    task="用户说想做什么"  # 用户的任务描述
)
```
系统会:建独立文件夹 → 清旧状态 → 新 Pipeline → 你就可以重新开始了。

**命名规则:** 简短描述性 + 日期(可选),让人一看就知道是什么项目。

## 铁律(违反即严重失职)

- 需要派任务 -> 直接调用 `dispatch_clone`,不要写成文本
- 需要问用户 -> 直接调用 `ask_user`
- 需要保存简报 -> 直接调用 `save_director_brief`
- **不确定就去搜**,拿不准就调用 `search_knowledge`
- **永远秒回用户**,不要等任何后台任务
- **每件事做完后,用 `get_pipeline_status` 或查看草稿确认有变化**,没变化就是空转
- **不要用文字描述"我要做什么"，直接调工具。** 说"先看素材"却不调 browse_memory = 没做 = 失职
- **工作流里的每一步都必须对应一个工具调用。** 没有对应工具调用的步骤等于没做。
- **勘探阶段(见第一步)根据情况走:索引已存在就 browse_memory,不存在才 audio_prospect/prospect_material。**
- **执行阶段用 dispatch_clone 派分身, 指定 tool_groups 就行。** 不需要指定具体工具。
- **分身有自主浏览文件能力(list_files/read_json/read_text)，不需要你告诉它路径。**
"""

from director.logging_config import get_logger
from director.storage import JsonStore
from director import config as clipmind_config
from director.exceptions import ConfigError

log = get_logger("server.runner")


class DirectorRunner:
    """Director 常驻 Agent"""

    # 线程本地存储:每个 runner 线程持有自己的 API 配置,取代全局 os.environ
    _thread_local = threading.local()

    def __init__(self, event_callback=None):
        self.event_callback = event_callback or (lambda e, d: None)
        self._thread = None
        self._ask_queue = queue.Queue()
        self._cancel_flag = threading.Event()
        self._preview_queue = queue.Queue()
        self._running = False
        self._agent_busy = False  # Director 正在回复中
        # 导演模型优先 LLM_MODEL 环境变量，否则从模型注册表读取
        _default_model = "qwen3.6-plus"
        try:
            from director.config import get_model_for_role
            _default_model = get_model_for_role("director")
        except Exception:
            pass
        self._config = {
            "base_url": os.environ.get(
                "LLM_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            "api_key": "",
            "model": os.environ.get("LLM_MODEL", _default_model),
        }

        # Pipeline 实例(创建素材后初始化)
        self._pipeline = None

        # 聊天历史
        self.chat_history: list[dict] = []
        self._MAX_HISTORY = 60  # 最多保留 30 轮对话

        # 物料状态
        self._materials = []        # 当前素材路径
        self._draft_id = ""         # 草稿 ID
        self._project_name = ""     # 项目名(用于文件路径对齐)
        self._task = ""             # 用户原始任务描述
        self._pipeline_started = False  # 是否已启动 Pipeline
        self._completed_stages = []     # 已完成的阶段

        # Agent 断路器(运行时级)
        self._agent_failures = {}       # agent_name -> 连续失败次数
        self._MAX_AGENT_FAILURES = 5    # 连续失败 5 次 -> 自动跳过（网络抖动容忍）

        # Idle 检测
        self._last_user_msg_time = 0.0

        # Pipeline 启动标记(由 send_message -> _run_director_agent 检测,加锁防竞态)
        self._pending_start = False
        self._pending_start_lock = threading.Lock()
        self._idle_timer = None
        self._IDLE_TIMEOUT = 900  # 15 分钟（分析素材时可能长时间无用户消息）

        # 异步压缩状态
        self._compression_complete = threading.Event()
        self._compressed_paths: list[str] = []
        self._compression_error: str = ""

        # 对外暴露的项目上下文
        self.last_state = None

    def _emit(self, event_type: str, data: dict):
        """内部事件发射"""
        if self._cancel_flag.is_set():
            return
        try:
            self.event_callback(event_type, data)
        except Exception:
            pass

    def _append_history(self, role: str, content: str):
        """添加聊天记录"""
        entry = {"role": role, "content": content[:3000]}
        self.chat_history.append(entry)
        if len(self.chat_history) > self._MAX_HISTORY:
            self.chat_history = self.chat_history[-self._MAX_HISTORY:]

    def _get_history_for_llm(self) -> list[dict]:
        """获取格式化后的聊天历史(不含 system)"""
        return list(self.chat_history)

    def _start_idle_timer(self):
        """启动闲置超时检测"""
        self._stop_idle_timer()
        self._idle_timer = threading.Timer(self._IDLE_TIMEOUT, self._on_idle_timeout)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _restart_idle_timer(self):
        """重启闲置超时检测"""
        self._start_idle_timer()

    def _stop_idle_timer(self):
        """停止闲置超时检测"""
        if self._idle_timer:
            try:
                self._idle_timer.cancel()
            except Exception:
                pass
            self._idle_timer = None

    def _on_idle_timeout(self):
        """闲置超时回调"""
        self._emit("progress", {"status": "idle_timeout"})

    def _wait_for_answer(self, question: str) -> str:
        """等待用户回答问题(阻塞,最多 120 秒)"""
        try:
            return self._ask_queue.get(timeout=120)
        except queue.Empty:
            return "(用户未在 120 秒内回答,继续执行)"

    def configure(self, base_url: str = "", api_key: str = "", model: str = "",
                  backend_url: str = ""):
        """设置 API 配置(从 Electron 接收)

        直连模式: LLM 直接调用 DashScope,不经过代理.
        api_key = 用户 JWT token,仅用于用量上报.
        真正的 DashScope API Key 从环境变量 DASHSCOPE_API_KEY 或 config.json 读取.
        backend_url: ClipMind 后端地址（如 http://localhost:8765）
        """
        if base_url and "/api/proxy" not in base_url:
            self._config["base_url"] = base_url
            DirectorRunner._thread_local.base_url = base_url
        elif not base_url:
            # 空字符串 → 移除旧配置,让 _make_llm_func 回退到 env/config
            self._config.pop("base_url", None)
            if hasattr(DirectorRunner._thread_local, 'base_url'):
                del DirectorRunner._thread_local.base_url

        if api_key:
            # api_key 是 JWT,仅用于用量上报
            self._config["report_api_key"] = api_key
            DirectorRunner._thread_local.report_api_key = api_key
            # ⚠️ 不覆盖 DASHSCOPE_API_KEY 环境变量 — 保持真实的 DashScope Key

        if model:
            self._config["model"] = model
            DirectorRunner._thread_local.model = model

        if backend_url:
            self._config["backend_url"] = backend_url
            DirectorRunner._thread_local.backend_url = backend_url

        # 直连模式: 不再配置 dashscope SDK 代理

    @staticmethod
    def _configure_dashscope_sdk(backend_url: str, api_key: str):
        """设置 dashscope SDK 走代理"""
        try:
            import dashscope
            # dashscope 原生 API: MultiModalConversation.call() 等
            dashscope.api_key = api_key
            dashscope.base_http_api_url = f"{backend_url}/api/proxy/api/v1"
            # 环境变量回退（供子进程 / requests 直调）
            os.environ["DASHSCOPE_API_KEY"] = api_key
            os.environ["DASHSCOPE_API_BASE"] = f"{backend_url}/api/proxy"
            os.environ["OPENAI_API_KEY"] = api_key
            os.environ["LLM_BASE_URL"] = f"{backend_url}/api/proxy/compatible-mode/v1"
            log.info("dashscope SDK 已配置为走代理: %s", backend_url)
        except ImportError:
            log.warning("dashscope SDK 未安装，VL 工具可能不可用")
        except Exception as e:
            log.warning("配置 dashscope SDK 失败: %s", e)

    def _setup_tools_and_env(self):
        """设置环境变量和线程本地存储(被 start_project 等方法调用)

        直连模式: 不覆盖 DASHSCOPE_API_KEY,只传递用量上报配置.
        """
        cfg = self._config
        DirectorRunner._thread_local.base_url = cfg.get("base_url", "")
        DirectorRunner._thread_local.model = cfg.get("model", "")

        # 用量上报配置
        report_key = cfg.get("report_api_key", "")
        if report_key:
            DirectorRunner._thread_local.report_api_key = report_key
        backend_url = cfg.get("backend_url", "")
        if backend_url:
            DirectorRunner._thread_local.backend_url = backend_url

        # ⚠️ 不覆盖 DASHSCOPE_API_KEY — 保持真实的 DashScope Key
        # 只在 CLIPMIND 命名空间下传上报凭证
        os.environ.setdefault("CLIPMIND_BASE_URL", cfg.get("base_url", ""))
        os.environ.setdefault("CLIPMIND_REPORT_API_KEY", report_key)
        if backend_url:
            os.environ.setdefault("CLIPMIND_BACKEND_URL", backend_url)

        # 直连模式: 不再配置 dashscope SDK 代理

    # ── 公开接口 ──

    def start_project(self, video_paths: list[str], task: str = "", draft_id: str = "", project_name: str = ""):
        """用户导入素材 -> 创建 Pipeline 实例 + Director 就位"""
        # 幂等:如果 Pipeline 已存在,只更新素材,不重新创建
        if self._pipeline:
            self._materials = copy.copy(video_paths)
            self._task = task or self._task
            self._draft_id = draft_id or self._draft_id
            self._project_name = project_name or self._project_name
            self._pipeline.video_paths = [os.path.abspath(p) for p in video_paths]
            self._pipeline.state.video_paths = [os.path.abspath(p) for p in video_paths]
            self._pipeline.state.save()
            log.info("素材已更新: %d 个文件", len(video_paths))
            return

        self._cancel_flag.clear()

        # 保存物料
        self._materials = copy.copy(video_paths)
        self._task = task
        self._draft_id = draft_id
        self._project_name = project_name
        self._pipeline_started = False
        self._completed_stages = []

        # 加载工具
        self._setup_tools_and_env()
        from director.pipeline import MultiStagePipeline

        # 创建 Pipeline 实例(只初始化工作区,不运行)
        try:
            # 事件回调:Pipeline 事件 -> 转给 UI
            def on_pipeline_event(event_type: str, data: dict):
                if self._cancel_flag.is_set():
                    return
                self._emit(event_type, data)

            self._pipeline = MultiStagePipeline(
                video_paths=video_paths,
                task=task,
                verbose=False,
                on_event=on_pipeline_event,
                director_brief="",  # 简报由 Director 后续写入
                project_name=self._project_name,  # 草稿目录对齐项目名
            )
        except Exception as e:
            log.exception("Pipeline 创建失败")
            self._emit("error", {"message": f"Pipeline 创建失败: {e}"})
            return

        # Director 开始工作
        self._running = True
        self._emit("progress", {
            "status": "started",
            "paths": video_paths,
            "stage": "导演准备中",
        })

        # 给用户打招呼
        if task:
            welcome = f"收到 {len(video_paths)} 个素材,我是你的剪辑导演. 你说「{task[:100]}」,聊聊具体想要什么效果?"
        else:
            welcome = (
                f"收到 {len(video_paths)} 个素材,我是你的 AI 剪辑导演——ClipMind 的核心.\n\n"
                f"你可以告诉我你想剪成什么样的视频，我来出方案、派助手帮你完成。\n"
                f"还没想法？跟我说说这批素材是什么内容，我帮你出主意。"
            )
        self._emit("ai_message", {"content": welcome})
        self._append_history("assistant", welcome)

        # 启动 idle 检测
        self._last_user_msg_time = time.time()
        self._start_idle_timer()

    def send_message(self, text: str):
        """用户发消息 -> Director 回复(非阻塞,发起后台 Agent)"""
        if self._agent_busy:
            self._emit("error", {"message": "Director 正在思考,请稍等"})
            return

        self._last_user_msg_time = time.time()
        self._restart_idle_timer()

        # 记录用户消息
        self._append_history("user", text)

        # 后台启动 Director Agent 回复
        self._agent_busy = True
        self._emit("progress", {"status": "thinking", "stage": "导演思考中"})
        self._thread = threading.Thread(
            target=self._run_director_agent,
            args=(text,),
            daemon=True,
        )
        self._thread.start()

    def respond_ask(self, text: str):
        """回答 Director 提出的问题"""
        self._ask_queue.put(text)

    def respond_preview_clip(self, data: str):
        """Electron 回复预览片段数据(base64 WebM)"""
        self._preview_queue.put(data)

    def start_pipeline(self):
        """用户点"开始" -> 素材分析 + Director 规划各阶段后逐阶段执行(非阻塞)"""
        if self._pipeline_started:
            self._emit("error", {"message": "Pipeline 已启动"})
            return
        if not self._pipeline:
            self._emit("error", {"message": "请先导入素材"})
            return

        self._pipeline_started = True

        # 防重复进入
        if self._agent_busy:
            with self._pending_start_lock:
                if self._pending_start:
                    return
                self._pending_start = True
            return

        self._agent_busy = True
        log.info("start_pipeline: 启动 Director 驱动管线")
        self._thread = threading.Thread(
            target=self._run_director_pipeline,
            daemon=True,
        )
        self._thread.start()

    def chat(self, text: str):
        """纯聊天(无素材)"""
        if self._agent_busy:
            self._emit("error", {"message": "Director 正在思考,请稍等"})
            return

        self._append_history("user", text)
        self._agent_busy = True
        self._thread = threading.Thread(
            target=self._run_chat_agent,
            args=(text,),
            daemon=True,
        )
        self._thread.start()

    def cancel(self):
        """取消所有操作"""
        self._cancel_flag.set()
        self._agent_busy = False
        self._running = False
        self._pipeline_started = False
        self._stop_idle_timer()
        self._emit("progress", {"status": "cancelled"})

    # ── 分析接口(兼容旧版) ──

    def analyze(self, video_paths: list[str], intent: str = ""):
        """分析素材并生成方案(同步).兼容旧接口,转为 start_project + chat."""
        self.start_project(video_paths, intent)

    def confirm_plan(self):
        """用户确认方案 -> 启动 Pipeline.兼容旧接口."""
        self.start_pipeline()

    @property
    def is_running(self) -> bool:
        return self._running

    def wait(self, timeout=None):
        """等待当前任务完成(阻塞)"""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ── 内部:Director Agent ──

    def _run_director_agent(self, user_text: str):
        """Director 回复用户消息(用 agent_loop,带工具)"""
        try:
            self._setup_tools_and_env()
            from director.agent_loop import agent_loop, ToolDef, AgentState
            from director.pipeline import _make_llm_func
            stream_cb = lambda c: self._emit("stream_chunk", {"content": c})
            llm_func = _make_llm_func(stream_callback=stream_cb)

            # 对话 Agent 只给聊天相关工具,不给 dispatch_clone/command 等管线工具
            all_tools = self._build_director_tools()
            chat_only_names = {"save_director_brief", "ask_user", "start_new_project"}
            tools = [t for t in all_tools if t.name in chat_only_names]

            # 构建 information payload(非 system prompt,仅给这次调用做上下文)
            info_blocks = []
            if self._pipeline:
                info_blocks.append(
                    f"当前素材: {len(self._materials)} 个\n"
                    + "\n".join(f"- {os.path.basename(p)}" for p in self._materials)
                )
            if self._pipeline_started:
                info_blocks.append(f"管线进度: {' -> '.join(self._completed_stages) if self._completed_stages else '刚开始'}")
            info = "\n\n".join(info_blocks) if info_blocks else ""

            # 合并:system prompt + 聊天历史
            history = self._get_history_for_llm()
            # system prompt 由 agent_loop 注入
            # history 注入在 system 和 task 之间
            # task 是本次调用的具体任务

            task_parts = [f"用户说: {user_text}"]
            if info:
                task_parts.append(f"\n\n当前状态:\n{info}")
            task_parts.append(
                "\n\n回复用户.如果需要,用 ask_user 和用户聊出方向."
                "聊得差不多后,用 save_director_brief 保存简报."
                "做完了说'完成'."
            )
            task = "\n".join(task_parts)

            from director.tool_catalog import get_catalog_text
            catalog_text = get_catalog_text()
            system_prompt = DIRECTOR_SYSTEM_PROMPT.format(TOOL_CATALOG=catalog_text)

            state = agent_loop(
                system_prompt=system_prompt,
                task=task,
                tools=tools,
                llm_func=llm_func,
                max_turns=5,  # 足够让 Director 答一句+调一个工具
                verbose=False,
                on_event=lambda e, d: self._emit(e, d) if not self._cancel_flag.is_set() else None,
                is_cancelled=lambda: self._cancel_flag.is_set(),
                history=history,
                require_render=False,
            )

            # 记录 AI 输出到历史
            if state.final_reasoning:
                self._append_history("assistant", state.final_reasoning[:2000])

            # 检查是否有等待的 pipeline start
            should_start = False
            with self._pending_start_lock:
                if self._pending_start and not self._cancel_flag.is_set():
                    self._pending_start = False
                    should_start = True
                    log.info("_run_director_agent: _pending_start 为 True,将启动管线")
            if should_start:
                self._run_director_pipeline()

        except KeyboardInterrupt:
            self._emit("progress", {"status": "cancelled"})
        except Exception as e:
            log.exception("Director Agent 回复失败")
            self._emit("error", {"message": f"Director 回复失败: {type(e).__name__}: {str(e)[:200]}"})
        finally:
            self._agent_busy = False
            self._emit("progress", {"status": "idle", "stage": "就绪"})

    def _run_chat_agent(self, text: str):
        """纯聊天(无素材,无工具)"""
        try:
            self._setup_tools_and_env()
            from director.pipeline import _make_llm_func
            stream_cb = lambda c: self._emit("stream_chunk", {"content": c})
            llm_func = _make_llm_func(stream_callback=stream_cb)

            history = self._get_history_for_llm()
            # 纯聊天模式不加载工具目录，但需要去掉 {TOOL_CATALOG} 占位符
            prompt = DIRECTOR_SYSTEM_PROMPT.replace("{TOOL_CATALOG}", "（纯聊天模式，无工具）")
            messages = [{"role": "system", "content": prompt}] + list(history)

            resp = llm_func(messages, [])
            content = resp.content if hasattr(resp, 'content') else str(resp)
            self._emit("ai_message", {"content": content})
            self._append_history("assistant", content[:2000])

        except Exception as e:
            log.exception("聊天模式 LLM 调用失败")
            self._emit("error", {"message": f"{type(e).__name__}: {str(e)}"})
        finally:
            self._agent_busy = False

    # ── 内部:Director 驱动管线 ──

    @staticmethod
    def _compress_material(video_path: str) -> bool:
        """压缩单个原始素材到720p(整文件,不切分)"""
        import subprocess, os
        from director.tools.cut import _find_draft_dir

        work_dir = _find_draft_dir()
        compressed_dir = os.path.join(work_dir, "compressed_originals")
        os.makedirs(compressed_dir, exist_ok=True)

        base = os.path.splitext(os.path.basename(video_path))[0]
        out_path = os.path.join(compressed_dir, f"{base}_720p.mp4")

        if os.path.exists(out_path) and os.path.getsize(out_path) > 10240:
            return True  # 已压缩过

        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path,
                 "-vf", "scale='min(1280,iw)':'min(720,ih)'",
                 "-c:v", "libx264", "-crf", "28", "-preset", "fast",
                 "-c:a", "aac", "-b:a", "128k",
                 out_path],
                capture_output=True, timeout=1800,
            )
            return os.path.exists(out_path) and os.path.getsize(out_path) > 10240
        except Exception as e:
            log.warning("压缩失败 %s: %s", video_path, e)
            return False

    def _compress_all_background(self):
        """后台压缩所有素材(异步线程)"""
        self._compression_complete.clear()
        self._compressed_paths = []
        self._compression_error = ""

        compressed_count = 0
        for mp in self._materials:
            if self._cancel_flag.is_set():
                return
            try:
                if self._compress_material(mp):
                    compressed_count += 1
                else:
                    log.warning("压缩失败: %s", mp)
            except Exception as e:
                log.warning("压缩异常 %s: %s", mp, e)
                self._compression_error = f"{mp}: {e}"

        # 扫描压缩目录收集路径
        from director.tools.cut import _find_draft_dir
        compressed_dir = os.path.join(_find_draft_dir(), "compressed_originals")
        if os.path.exists(compressed_dir):
            for f in sorted(os.listdir(compressed_dir)):
                if f.endswith("_720p.mp4"):
                    full = os.path.join(compressed_dir, f)
                    if os.path.getsize(full) > 10240:
                        self._compressed_paths.append(full)

        log.info("后台压缩完成: %d/%d 个素材, %d个可用文件",
                 compressed_count, len(self._materials), len(self._compressed_paths))
        self._emit("progress", {"status": "preprocessing", "stage": f"后台压缩完成({compressed_count}/{len(self._materials)})"})
        self._compression_complete.set()

    def _get_compressed_paths_text(self) -> str:
        """生成压缩状态文本(给 Director 看)"""
        if self._compression_complete.is_set():
            if not self._compressed_paths:
                return "  ⚠️ 压缩似乎未产生可用文件"
            lines = ["✅ 压缩已完成,文件路径(传给 prospect_material 的 video_path):"]
            for cp in self._compressed_paths:
                lines.append(f"  - {cp}")
            return "\n".join(lines)
        else:
            return "  ⏳ 视频压缩进行中,请先用 audio_prospect 做语音勘探"

    def _get_raw_material_paths_text(self) -> str:
        """生成原始素材路径文本(给 Director 传 audio_prospect 用)"""
        lines = ["原始素材路径(传给 audio_prospect 的 video_path,立即可用):"]
        for mp in self._materials:
            lines.append(f"  - {mp}")
        return "\n".join(lines)

    def _run_director_pipeline(self):
        """Director 驱动管线(异步压缩+Director立即启动)

        流程:
          1. 后台启动压缩(不阻塞)
          2. Director 立即启动,先用 audio_prospect 听语音
          3. 压缩完成后,再用 prospect_material 看画面
          4. 勘探完了写剧本→拆角色→执行→验收→出片
        """
        if not self._pipeline:
            return

        try:
            self._setup_tools_and_env()

            # ── 拉起 AI 搜索守护进程(open-websearch) ──
            try:
                from director.tools.web_search import ensure_daemon
                daemon_url = ensure_daemon()
                if daemon_url:
                    log.info(f"✅ AI搜索守护进程已就绪: {daemon_url}")
                else:
                    log.warning("⚠️ AI搜索守护进程未启动,将使用备用搜索方案")
            except Exception as e:
                log.warning(f"⚠️ AI搜索守护进程启动失败: {e}")

            # ── 启动后台压缩(不阻塞Director) ──
            self._emit("progress", {"status": "preprocessing", "stage": "后台压缩启动(导演可先听语音)"})
            compression_thread = threading.Thread(target=self._compress_all_background, daemon=True)
            compression_thread.start()
            log.info("后台压缩已启动,Director 立即开始工作")

            # ── 重建管线状态 ──
            from director.pipeline_state import PipelineState
            self._pipeline.state = PipelineState(self._pipeline.work_dir)

            from director.agent_loop import agent_loop
            from director.pipeline import _make_llm_func

            # 构建 LLM 和工具
            stream_cb = lambda c: self._emit("stream_chunk", {"content": c})
            llm_func = _make_llm_func(stream_callback=stream_cb)
            tools = self._build_director_tools()

            # 素材信息
            material_list = "\n".join(
                f"  - {os.path.basename(p)} (原始文件,{os.path.getsize(p)//1024//1024}MB)"
                for p in self._materials
            ) if self._materials else "  (无素材)"

            brief = getattr(self._pipeline.state, 'director_brief', '')
            if not brief:
                brief = "(导演未给出具体方向)"

            # 构建任务:原始路径 + 压缩状态(动态更新)
            compressed_status = self._get_compressed_paths_text()
            raw_paths = self._get_raw_material_paths_text()

            task_parts = [
                "## 新项目启动\n",
                f"## 素材列表\n{material_list}\n",
                f"## 原始素材路径(音频勘探用)\n{raw_paths}\n",
                f"## 压缩状态\n{compressed_status}\n",
                f"## 导演方向\n{brief}\n",
                "  ✅ Draft(\"main\") 已创建\n",
                "## 执行清单(依次执行,每步必须调对应工具)\n",
                "### ⚠️ 勘探阶段(第1-2步)严禁用 dispatch_clone！必须亲自调工具\n",
                "   dispatch_clone 只能用于执行阶段.勘探阶段一步都不能用.\n",
                "   亲自调 = 你自己直接调 audio_prospect / prospect_material 工具.\n",
                "   派分身(dispatch_clone)去做勘探 = 违规,等于没做.\n",
                "\n",
                "### 第1步: 语音勘探(亲自调,不可 dispatch_clone)\n",
                "  亲自调两遍 audio_prospect(原始素材路径),每个素材一遍.\n",
                "  压缩没完成也不影响,audio_prospect 用的是原始素材.\n",
                "### 第2步: 画面勘探(亲自调,不可 dispatch_clone)\n",
                "  亲自调两遍 prospect_material(压缩文件路径),每个素材一遍.\n",
                "  这是核心勘探工具,必须调.不要用 get_video_metadata 代替.\n",
                "### 第3步: 写剧本\n",
                "  基于勘探结果写剧本.剧本素材必须是勘探中确认有的.\n",
                "### 第4步: 写项目计划书\n",
                "  剧本拆成可执行的任务,每个任务指定 tool_groups.\n",
                "### 第5步: 按计划派分身执行\n",
                "  逐个 dispatch_clone → 验收 → 再派下一个.\n",
                "  用 tool_groups 指定分组,不用 tool_names.\n",
                "  从工具目录里选分组,例如 ['画面与场景', '语音与转写'].\n",
                "  ⚠️ 素材分析类任务:后台已压缩好(compressed_originals/*_720p.mp4),\n",
                "     场景切分和批量分析都基于压缩文件,不再调 compress_segments.\n",
                "  ⚠️ 任务要具体:告诉分身用什么文件、产出什么.\n",
                '  例: dispatch_clone(mission="分析场景", tool_groups=["画面与场景","语音与转写"])\n',
                "  →逐个 dispatch_clone→验收→渲染审片.\n",
            ]
            task = "\n".join(task_parts)

            from director.tool_catalog import get_catalog_text
            catalog_text = get_catalog_text()
            system_prompt = DIRECTOR_SYSTEM_PROMPT.format(TOOL_CATALOG=catalog_text)

            history = self._get_history_for_llm()
            state = agent_loop(
                system_prompt=system_prompt,
                task=task,
                tools=tools,
                llm_func=llm_func,
                max_turns=80,
                verbose=False,
                on_event=lambda e, d: self._emit(e, d) if not self._cancel_flag.is_set() else None,
                is_cancelled=lambda: self._cancel_flag.is_set(),
                history=history,
                require_render=False,
                min_tool_calls=2,
            )

            # 记录 AI 输出到历史
            if state.final_reasoning:
                self._append_history("assistant", state.final_reasoning[:2000])

            # 等待压缩完成(如果 Director 先做完)
            if not self._compression_complete.is_set() and not self._cancel_flag.is_set():
                log.info("Director 已完成,等待压缩线程结束...")
                compression_thread.join(timeout=600)

            # 完成通知
            if not self._cancel_flag.is_set():
                draft_id = self._draft_id or getattr(self._pipeline.state, 'draft_id', '')
                self._emit("ai_message", {
                    "content": f"**剪辑完成!**\n草稿 ID: {draft_id}",
                })
                self._emit("project_complete", {
                    "draft_id": draft_id,
                    "output_path": "",
                    "turns": state.turns_used if hasattr(state, 'turns_used') else 0,
                    "tokens": 0,
                    "stages": [],
                })

        except Exception as e:
            log.exception("Director 驱动管线异常")
            self._emit("error", {"message": f"管线异常: {type(e).__name__}: {str(e)[:200]}"})
        finally:
            self._pipeline_started = False
            self._agent_busy = False

    # ── 内部:工具 ──

    def _build_director_tools(self) -> list:
        """构建 Director 可用的工具列表(将军模式:只下命令+派分身,不动手)"""
        from director.agent_loop import ToolDef
        from director.executor import get_layer_names_str
        from director.tool_catalog import get_catalog_text

        def _save_brief(brief: str) -> str:
            """保存导演简报(写入 PipelineState)"""
            if not self._pipeline:
                return "❌ Pipeline 未创建"
            from director.pipeline_state import PipelineState
            st = PipelineState(self._pipeline.work_dir)
            st.director_brief = brief
            st.save()
            self._pipeline.state.director_brief = brief
            log.info("导演简报已保存 (%d 字符)", len(brief))
            return f"✅ 简报已保存,共 {len(brief)} 字符"

        def _dispatch_clone(mission: str = "", tool_groups: str = "", tool_names: str = "",
                           params: str = "", done_when: str = "",
                           parallel_missions: str = "") -> str:
            """【核心工具】派分身去执行具体任务.

            分身自动获得指定分组的工具,不需要的组不加载.
            用 tool_groups 指定分组名(逗号分隔),从工具目录里选.

            支持并行派发:传 parallel_missions 参数(JSON 数组),同时派多个互不依赖的分身.

            Args:
                mission: 任务描述(自然语言,越具体越好)
                tool_groups: 逗号分隔的工具分组名列表,如 "画面与场景,语音与转写"
                             分身自动获得这些组的所有工具(每组 8-15 个)
                tool_names: (旧模式)逗号分隔的工具名列表,不指定 tool_groups 时用
                params: 可选参数 JSON
                done_when: 完成标准描述
                parallel_missions: JSON 数组,每个元素为 {"role_name","mission","tool_groups","done_when"}
                                  当传此参数时,同时派发所有分身,全部完成后返回各自结果.
            """
            if not self._pipeline:
                return "[错误] Pipeline 未创建"

            from director.executor import run_executor
            from concurrent.futures import ThreadPoolExecutor, as_completed

            # ── 并行模式 ──
            if parallel_missions:
                try:
                    missions = json.loads(parallel_missions)
                except json.JSONDecodeError as e:
                    return f"[错误] parallel_missions 不是有效 JSON: {e}"
                if not isinstance(missions, list) or len(missions) < 2:
                    return "[错误] parallel_missions 必须是至少 2 个元素的 JSON 数组"
                if len(missions) > 5:
                    missions = missions[:5]  # 最多 5 个并行

                def _run_one(m: dict) -> dict:
                    """在子线程中执行一个分身任务"""
                    _groups = [g.strip() for g in m.get("tool_groups", "").split(",") if g.strip()]
                    return run_executor(
                        agent_type="分身",
                        mission=m.get("mission", ""),
                        params={},
                        pipeline=self._pipeline,
                        tool_groups=_groups or None,
                        done_when=m.get("done_when", ""),
                        verbose=True,
                        on_event=lambda e, d: self._emit(e, d)
                            if not self._cancel_flag.is_set() else None,
                        is_cancelled=lambda: self._cancel_flag.is_set(),
                    )

                log.info("[Dispatch] 并行派发 %d 个分身...", len(missions))
                self._emit("progress", {"status": "executor_busy", "agent": "分身(并行)",
                                        "count": len(missions)})

                all_results = []
                with ThreadPoolExecutor(max_workers=len(missions)) as pool:
                    futures = {pool.submit(_run_one, m): m for m in missions}
                    for future in as_completed(futures):
                        m = futures[future]
                        try:
                            result = future.result()
                            all_results.append((m.get("role_name", "?"), result))
                        except Exception as e:
                            all_results.append((m.get("role_name", "?"),
                                                {"completed": False, "error": str(e)[:500]}))

                # 汇总结果
                parts = [f"✅ 并行派发完成 ({len(missions)} 个分身):"]
                for role, r in all_results:
                    status = "✅" if r.get("completed") else "❌"
                    turns = r.get("turns", 0)
                    elapsed = r.get("elapsed", 0)
                    summary = r.get("summary", "")[:200]
                    parts.append(f"\n[{status} {role}] {turns}turns, {elapsed:.0f}s")
                    if summary:
                        parts.append(f"  {summary}")
                    if not r.get("completed"):
                        parts.append(f"  ⚠️ {r.get('error', '未知错误')}")
                return "\n".join(parts)

            # ── 串行模式(原逻辑) ──
            # 断路器检查
            fail_count = self._agent_failures.get("_dispatch_clone", 0)
            if fail_count >= self._MAX_AGENT_FAILURES:
                msg = f"[断路器] dispatch_clone 连续失败 {fail_count} 次,自动跳过."
                log.warning("Circuit breaker tripped: dispatch_clone failures=%d", fail_count)
                return msg

            # 解析参数
            tool_list = None
            groups_list = None
            if tool_groups:
                groups_list = [t.strip() for t in tool_groups.split(",") if t.strip()]
            if not groups_list and tool_names:
                tool_list = [t.strip() for t in tool_names.split(",") if t.strip()]

            parsed_params = {}
            if params:
                try:
                    parsed_params = json.loads(params)
                except json.JSONDecodeError:
                    parsed_params = {"raw": params}

            self._emit("progress", {"status": "executor_busy", "agent": "分身", "mission": mission[:100]})

            result = run_executor(
                agent_type="分身",
                mission=mission,
                params=parsed_params,
                pipeline=self._pipeline,
                tool_names=tool_list,
                tool_groups=groups_list,
                done_when=done_when,
                verbose=True,
                on_event=lambda e, d: self._emit(e, d) if not self._cancel_flag.is_set() else None,
                is_cancelled=lambda: self._cancel_flag.is_set(),
            )

            log.info("[Dispatch] turns=%d, completed=%s, error=%s, elapsed=%.1fs",
                     result.get("turns", 0), result.get("completed"),
                     result.get("error", "")[:100] or "无",
                     result.get("elapsed", 0))

            if result.get("completed"):
                self._agent_failures["_dispatch_clone"] = 0
                parts = [f"[成功] 分身执行完毕"]
                summary = result.get("summary", "")
                if summary:
                    parts.append(f"\n\n{summary[:2000]}")
                return "".join(parts)
            else:
                self._agent_failures["_dispatch_clone"] = fail_count + 1
                error = result.get("error", "未知错误")
                return f"[失败] 分身执行失败: {error}"

        def _command(agent: str, mission: str, params: str = "") -> str:
            """效果层专用命令(仅接受 '动效层').非效果层任务用 dispatch_clone."""
            if agent not in ("动效层",):
                return f"[拒绝] command 仅用于效果层('动效层'),{agent} 不是效果层.非效果层任务请用 dispatch_clone(mission=..., tool_groups=..., ...)"
            if not self._pipeline:
                return "[错误] Pipeline 未创建"

            # 断路器检查
            fail_count = self._agent_failures.get(agent, 0)
            if fail_count >= self._MAX_AGENT_FAILURES:
                msg = (
                    f"[断路器] {agent} 连续失败 {fail_count} 次,自动跳过."
                    f"如仍需此层级,清理后再试."
                )
                log.warning("Circuit breaker tripped: agent=%s failures=%d", agent, fail_count)
                return msg

            from director.executor import run_executor

            parsed_params = {}
            if params:
                try:
                    parsed_params = json.loads(params)
                except json.JSONDecodeError:
                    parsed_params = {"raw": params}

            self._emit("progress", {"status": "executor_busy", "agent": agent, "mission": mission[:100]})

            result = run_executor(
                agent_type=agent,
                mission=mission,
                params=parsed_params,
                pipeline=self._pipeline,
                verbose=False,
                on_event=lambda e, d: self._emit(e, d) if not self._cancel_flag.is_set() else None,
                is_cancelled=lambda: self._cancel_flag.is_set(),
            )

            log.info("[Command] %s: turns=%d, completed=%s, error=%s, elapsed=%.1fs",
                     agent, result.get("turns", 0), result.get("completed"),
                     result.get("error", "")[:100] or "无",
                     result.get("elapsed", 0))

            # 更新断路器状态
            if result.get("completed"):
                self._agent_failures[agent] = 0
                parts = [f"[成功] {agent} 执行完毕"]
                summary = result.get("summary", "")
                if summary:
                    parts.append(f"\n\n{summary[:2000]}")
                return "".join(parts)
            else:
                self._agent_failures[agent] = fail_count + 1
                error = result.get("error", "未知错误")
                return f"[失败] {agent} 执行失败: {error}"

        def _pipeline_status() -> str:
            """查看 Pipeline 整体状态"""
            if not self._pipeline:
                return "Pipeline 未创建"
            status = {
                "materials": len(self._materials),
                "pipeline_started": self._pipeline_started,
                "draft_id": self._draft_id or "",
            }
            try:
                d = self._pipeline.state._data
                analy = d.get("material_analysis", {})
                status["analysis_elapsed"] = analy.get("elapsed", 0)
                status["chunk_count"] = analy.get("chunk_count", 0)
            except Exception:
                pass
            try:
                staged_log = getattr(self._pipeline, 'stage_log', [])
                status["stage_log"] = staged_log
            except Exception:
                pass
            return json.dumps(status, ensure_ascii=False, indent=2)

        def _ask_user(question: str, options: str = "") -> str:
            """向用户提问"""
            self._emit("ask_user", {"question": question, "options": options})
            return self._wait_for_answer(question)

        def _search_knowledge(query: str) -> str:
            """搜索互联网,查找剪辑相关的知识,术语,最新做法.
            在你拿不准的时候用.比如不确定某个术语是否存在,某个功能怎么做,
            某个流程对不对.搜完再看结果决定怎么下命令.

            底层使用 curl_cffi 的 Chrome TLS 指纹伪装，能绕过搜索引擎反爬。
            同时支持中英文搜索，能找到知乎、CSDN、博客等中文文字文章。
            优先搜中文，搜不到会自动换语言再试。

            ⚠️ B站硬性规章: 此工具绝不返回B站视频结果。
            即使兜底到B站搜索，也只返回专栏文章，不返回视频。
            不要把B站视频当作知识来源。

            Args:
                query: 搜索关键词.中英文都行.根据当前素材类型自主决定搜索词。
            """
            from director.tools.web_search import search_all

            # DuckDuckGo HTML + curl_cffi TLS 指纹（主力，中英文皆可）
            result = search_all(query, limit=6)
            if result and "(搜索无结果)" not in result:
                return result

            # 都搜不到...
            return f"(搜索无结果,关键词: {query})"

        def _report_tool_gap(tool_name: str, needed_for: str, suggested_group: str = "") -> str:
            """报告管线中缺失的工具,保存到 tool_gaps.json 供开发参考."""
            import json as _json, os as _os
            gaps_path = _os.path.join(self._pipeline.work_dir, "tool_gaps.json") if self._pipeline else "tool_gaps.json"
            try:
                if _os.path.exists(gaps_path):
                    with open(gaps_path, "r", encoding="utf-8") as f:
                        gaps = _json.load(f)
                else:
                    gaps = []
            except Exception:
                gaps = []
            entry = {
                "tool_name": tool_name,
                "needed_for": needed_for,
                "suggested_group": suggested_group,
            }
            if entry not in gaps:
                gaps.append(entry)
                with open(gaps_path, "w", encoding="utf-8") as f:
                    _json.dump(gaps, f, ensure_ascii=False, indent=2)
            return f"[已记录] 工具缺口: {tool_name} ({needed_for})"

        def _browse_memory(target: str = "") -> str:
            """浏览记忆存储——直接看内容,不做关键词搜索.
            不传参数=粗看(文件概览),传文件名=细看(完整内容).
            """
            if not self._pipeline:
                return "(无项目)"
            from director.memory_store import browse_memory as _bm
            return _bm(target=target)

        def _search_memory(query: str) -> str:
            """在项目索引中搜索之前分身产出的分析结果.

            搜索范围:ASR 转写、画面描述、场景标签、历史分身的任务记录.
            """
            if not self._pipeline:
                return "(无项目)"
            from director.memory_store import search_index, get_index_summary
            try:
                results = search_index(self._pipeline.work_dir, query)
                if not results:
                    summary = get_index_summary(self._pipeline.work_dir)
                    if "分析 chunk 数" in summary:
                        fallback = search_index(self._pipeline.work_dir, "")
                        if fallback:
                            import json as _json
                            return (
                                f"(关键词「{query}」无匹配,返回全量索引内容:)\n"
                                + _json.dumps(fallback[:5], ensure_ascii=False, indent=2)
                            )
                    return f"(未在索引中找到「{query}」的匹配,索引为空)"
                import json as _json
                return _json.dumps(results, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"(索引查询失败: {e})"

        def _get_index_info() -> str:
            """查看当前项目索引概览(文件数/chunk数量/文件列表)"""
            if not self._pipeline:
                return "(无项目)"
            from director.memory_store import get_index_summary
            try:
                return get_index_summary(self._pipeline.work_dir)
            except Exception as e:
                return f"(索引查看失败: {e})"

        def _compress_segments() -> str:
            """压缩所有已切分的视频片段到720p CRF28，供AI视觉分析使用。

            这是分析前的标准预处理——所有视频都必须先压缩再分析。
            压缩后的片段只用于视觉分析，原始片段保留不变（最终渲染用原始片段）。
            """
            if not self._pipeline:
                return "[错误] Pipeline 未创建"
            from director.tools.scene import compress_segments as _cs
            return _cs()

        def _prospect_material(video_path: str) -> str:
            """勘探素材——快速判断类型和价值(需等压缩完成)"""
            if not self._pipeline:
                return "[错误] Pipeline 未创建"
            if not self._compression_complete.is_set():
                return "⏳ 压缩未完成,请先用 audio_prospect 做语音勘探,或用 compression_status 检查进度"
            from director.tools.prospect import prospect_material as _pm
            return _pm(video_path)

        def _compression_status() -> str:
            """检查视频压缩是否完成.压缩完成后才能用 prospect_material/watch_video 等画面工具."""
            if self._compression_complete.is_set():
                if self._compressed_paths:
                    names = "\n".join(f"  - {p}" for p in self._compressed_paths)
                    return f"✅ 压缩完成! 可用压缩文件:\n{names}"
                else:
                    return "⚠️ 压缩状态异常:未产生可用文件"
            else:
                return "⏳ 压缩进行中,请先用 audio_prospect 做语音勘探"

        def _start_new_project(project_name: str, video_paths: str, task: str = "") -> str:
            """启动全新项目——建独立文件夹,清旧状态,所有后续操作走新目录.

            当你发现用户发来全新的素材、或话题和当前项目完全无关时,
            调用此工具创建新项目,防止文件串到旧项目里.

            Args:
                project_name: 项目名(简短描述性名称).
                             系统用它建文件夹,取名要一看就知道是什么项目.
                video_paths: 逗号分隔的素材文件绝对路径
                task: 用户的任务描述
            """
            import json as _json
            safe_name = project_name.strip().replace(" ", "_")
            # 截断安全名
            safe_name = safe_name[:60]
            from director.workspace import get_project_dir, get_workspace_root
            from director.pipeline import MultiStagePipeline

            # 1. 解析素材路径
            paths = [p.strip() for p in video_paths.split(",") if p.strip()]
            if not paths:
                return "[错误] 至少需要一个素材文件路径"

            # 2. 检查路径是否存在
            exist_paths = []
            for p in paths:
                if os.path.exists(p):
                    exist_paths.append(p)
                else:
                    return f"[错误] 素材不存在: {p}"

            # 3. 停止旧管线(如果有)
            self._cancel_flag.set()
            self._pipeline_started = False
            self._completed_stages = []

            # 4. 创建新项目目录 + Pipeline
            project_dir = get_project_dir(safe_name)
            # 创建 pipeline 需要的子目录
            for sub in ("_index", "drafts", "stage_data", "compressed_originals",
                        "segments", "segments_compressed", "output"):
                os.makedirs(os.path.join(project_dir, sub), exist_ok=True)

            # 5. 设置环境变量指向新项目
            os.environ["CLIPMIND_PIPELINE_DIR"] = project_dir
            from director.pipeline_state import PipelineState

            def _on_pe(evt, dat):
                if not self._cancel_flag.is_set():
                    self._emit(evt, dat)

            new_pipeline = MultiStagePipeline(
                video_paths=exist_paths,
                task=task,
                verbose=False,
                on_event=_on_pe,
                director_brief="",
                project_name=safe_name,
            )
            self._pipeline = new_pipeline

            # 6. 更新 runner 状态
            self._materials = copy.copy(exist_paths)
            self._task = task
            self._project_name = safe_name
            self._draft_id = "main"
            self._cancel_flag.clear()
            self._running = True
            self._completed_stages = []
            self._pipeline_started = False
            self._agent_failures = {}
            self._compression_complete.clear()
            self._compressed_paths = []
            self._compression_error = ""

            # 7. 清空聊天历史(全新项目不保留旧对话)
            self.chat_history = []

            log.info("✅ 新项目创建: %s → %s", safe_name, project_dir)
            return (f"✅ 新项目「{safe_name}」已创建\n"
                    f"  文件夹: {project_dir}\n"
                    f"  素材: {len(exist_paths)} 个\n"
                    f"  任务: {task or '(未指定)'}\n\n"
                    f"现在可以开始做这个新项目了。请先 browse_memory 看素材类型。")

        def _audio_prospect(video_path: str) -> str:
            """语音勘探素材——直接用原始音频采样,不等压缩完成"""
            if not self._pipeline:
                return "[错误] Pipeline 未创建"
            from director.tools.audio_prospect import audio_prospect as _ap
            return _ap(video_path)

        def _save_agent_context(role_name: str, data_json: str) -> str:
            """保存当前角色的产出到上下文链（自动编号，无需指定序号）。

            每次 dispatch_clone 完成后，把分身的产出内容存下来。
            系统自动分配编号（ctx_001.json → ctx_002.json → ...），
            后续用 load_latest_context 取最新，或用 load_context_by_index 取指定序号。

            Args:
                role_name: 角色身份，如"内容分析师""编排师""自检员"
                data_json: 角色的产出数据（JSON 格式字符串）

            Returns:
                保存结果，含分配的编号
            """
            if not self._pipeline:
                return "[错误] Pipeline 未创建"
            try:
                data = json.loads(data_json)
            except json.JSONDecodeError:
                return "[错误] data_json 不是有效 JSON"
            from director.tools.video_context import save_context
            path = save_context(data, self._pipeline.work_dir, role=role_name)
            # 从路径提取编号
            import re
            m = re.search(r"ctx_(\d+)\.json", path)
            idx = m.group(1) if m else "?"
            return f"✅ #{idx} [{role_name}] 已保存 → {path}"

        def _load_latest_context() -> str:
            """加载上下文链中最新的角色产出。
            在派新任务前调用，拿到上一个角色的产出内容作为新角色的上下文。

            Returns:
                最新上下文的 JSON 文本
            """
            if not self._pipeline:
                return "[错误] Pipeline 未创建"
            from director.tools.video_context import load_latest_context, list_contexts
            data = load_latest_context(self._pipeline.work_dir)
            if data is None:
                return "(当前无上下文)"
            entries = list_contexts(self._pipeline.work_dir)
            latest_info = entries[-1] if entries else {}
            idx = latest_info.get("index", "?")
            role = latest_info.get("role", "?")
            header = f"// ← #ctx_{idx:03d} [{role}] 的最新上下文\n"
            return header + json.dumps(data, ensure_ascii=False, indent=2)

        def _load_context_by_index(index: int) -> str:
            """按序号加载指定角色的上下文。
            如果 ctx_001 是分析师、ctx_002 是编排师，
            调用 load_context_by_index(1) 拿到分析师的产出。

            Args:
                index: 序号，从 1 开始

            Returns:
                该序号对应的上下文 JSON 文本
            """
            if not self._pipeline:
                return "[错误] Pipeline 未创建"
            from director.tools.video_context import load_context, list_contexts
            data = load_context(self._pipeline.work_dir, index)
            if data is None:
                entries = list_contexts(self._pipeline.work_dir)
                existing = ", ".join(f"#{e['index']}" for e in entries) if entries else "(无)"
                return f"[未找到] 序号 #{index} 不存在. 当前可用: {existing}"
            entries = {e["index"]: e for e in list_contexts(self._pipeline.work_dir)}
            info = entries.get(index, {})
            role = info.get("role", "?")
            header = f"// #ctx_{index:03d} [{role}]\n"
            return header + json.dumps(data, ensure_ascii=False, indent=2)

        def _show_context_chain() -> str:
            """查看当前所有已保存的上下文链（编号 + 角色 + 时间）"""
            if not self._pipeline:
                return "[错误] Pipeline 未创建"
            from director.tools.video_context import get_context_chain_text
            return get_context_chain_text(self._pipeline.work_dir)

        tools = [
            ToolDef(
                name="save_director_brief",
                description="保存导演创作简报到管线状态.后续所有执行 Agent 都会读到这份简报.",
                fn=_save_brief,
                parameters={
                    "type": "object",
                    "properties": {
                        "brief": {
                            "type": "string",
                            "description": "导演创作简报.包含:视频类型,节奏基调,"
                                           "重点保留内容,技术容忍度,时长目标,BGM方向,剔除项",
                        },
                    },
                    "required": ["brief"],
                },
            ),
            ToolDef(
                name="dispatch_clone",
                description=("【核心工具】派分身去执行具体任务."
                           "指定 tool_groups 分组名,分身自动获得该组所有工具."
                           "每组只有 8-15 个工具,分身不会找不到."
                           "传 parallel_missions 可同时派多个互不依赖的分身."),
                fn=_dispatch_clone,
                parameters={
                    "type": "object",
                    "properties": {
                        "mission": {
                            "type": "string",
                            "description": "任务描述.用自然语言描述本次任务的目标和关注点."
                                           "越具体越好,例如'分析两个素材的场景片段,找出关键画面'"
                                           "而不是'分析素材'."
                                           "串行模式下必填,并行模式下可为空.",
                        },
                        "tool_groups": {
                            "type": "string",
                            "description": "逗号分隔的工具分组名.分身自动获得这些组的所有工具."
                                           "从工具目录里选分组,例如 '画面与场景,语音与转写'"
                                           "或 '裁切与提取' 或 '背景音乐与音效,音频处理'."
                                           "为空时用 tool_names 指定具体工具名.",
                        },
                        "tool_names": {
                            "type": "string",
                            "description": "(旧模式)逗号分隔的工具名列表.不指定 tool_groups 时用."
                                           "例如 'watch_video,batch_analyze'",
                        },
                        "params": {
                            "type": "string",
                            "description": "可选参数,JSON 格式."
                                           '例如 {"time_range": [300, 480]} 指定时间范围',
                        },
                        "done_when": {
                            "type": "string",
                            "description": "完成标准.明确描述做到什么程度算完成.",
                        },
                        "parallel_missions": {
                            "type": "string",
                            "description": "并行任务 JSON 数组,用于同时派发多个互不依赖的分身."
                                           "格式: [{\"role_name\":\"角色名\",\"mission\":\"任务描述\","
                                           "\"tool_groups\":\"工具分组\",\"done_when\":\"完成标准\"},...]"
                                           "系统同时运行所有角色,全部完成后返回各自结果."
                                           "用此参数时,mission/tool_groups 参数被忽略."
                                           "最多 5 个并行.",
                        },
                    },
                    "required": [],
                },
            ),
            ToolDef(
                name="command",
                description=("效果层专用命令.仅用于动效层(花字/动画/特效/转场/字幕)."
                           "非效果层任务请用 dispatch_clone."),
                fn=_command,
                parameters={
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "description": "执行层名称.目前仅支持 '动效层'.",
                        },
                        "mission": {
                            "type": "string",
                            "description": "任务简报.用自然语言描述本次任务的目标和关注点.",
                        },
                        "params": {
                            "type": "string",
                            "description": "可选参数,JSON 格式.",
                        },
                    },
                    "required": ["agent", "mission"],
                },
            ),
            ToolDef(
                name="get_pipeline_status",
                description="查看管线整体状态:素材数,草稿ID,已完成的分析阶段.",
                fn=_pipeline_status,
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            ToolDef(
                name="browse_memory",
                description="【推荐】浏览记忆存储——直接看内容,不做关键词搜索."
                           "粗看:browse_memory() 列出所有文件; "
                           "细看:browse_memory(target='文件名.json') 读完整内容.",
                fn=_browse_memory,
                parameters={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "文件名(可选).为空时返回概览(粗看),"
                                           "指定文件时返回该文件完整内容(细看)."
                                           "例如 'analysis_index.json' 或 'delegate_20250101_120000_xxx.json'",
                        },
                    },
                },
            ),
            ToolDef(
                name="search_memory",
                description="(备选)关键词搜索索引.中文匹配效果差,"
                           "建议先用 browse_memory 直接浏览.",
                fn=_search_memory,
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词.根据你的需求搜相关关键词。",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDef(
                name="compress_segments",
                description="【标准预处理】压缩所有已切分的视频片段到720p CRF28,供AI视觉分析使用."
                           "切分后分析前必须调用此工具——所有视频必须先压缩再分析."
                           "压缩后的片段只用于视觉分析,原始片段保留不变(最终渲染用原始素材).",
                fn=_compress_segments,
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            ToolDef(
                name="ask_user",
                description="暂停执行,向用户提问.用于和用户聊天,确认方向,展示简报.",
                fn=_ask_user,
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "你要问用户的问题"},
                        "options": {"type": "string", "description": "可选答案,逗号分隔"},
                    },
                    "required": ["question"],
                },
            ),
            ToolDef(
                name="search_knowledge",
                description="搜索互联网:查剪辑知识/术语/最新做法/流程."
                           "中英文双语搜索,搜不到中文会自动换英文."
                           "拿到新材料和用户需求后先搜一下这个类型怎么做,"
                           "分析报告出来后也搜一下编排方案.不要凭空编造.",
                fn=_search_knowledge,
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词.中英文都行。根据当前素材类型自主决定搜什么。",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDef(
                name="report_tool_gap",
                description="报告管线中缺失的工具:当你发现需要某个工具来完成计划,"
                           "但现有工具目录里找不到对应分组或功能时,用此工具记录下来."
                           "这样开发人员后续可以根据记录添加新工具.",
                fn=_report_tool_gap,
                parameters={
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "你希望使用的工具名称",
                        },
                        "needed_for": {
                            "type": "string",
                            "description": "这个工具用来做什么任务",
                        },
                        "suggested_group": {
                            "type": "string",
                            "description": "建议放到哪个工具分组下,如'音频处理','裁切与提取'",
                        },
                    },
                    "required": ["tool_name", "needed_for"],
                },
            ),
            ToolDef(
                name="save_agent_context",
                description="保存当前角色的产出到上下文链（自动编号）。"
                           "每次 dispatch_clone 完成后调用，把分身的产出内容存下来。"
                           "系统自动分配编号 ctx_001→ctx_002→...，不依赖角色名做 key，杜绝混乱。",
                fn=_save_agent_context,
                parameters={
                    "type": "object",
                    "properties": {
                        "role_name": {
                            "type": "string",
                            "description": "角色身份，如'内容分析师''节奏师''编排师''自检员'",
                        },
                        "data_json": {
                            "type": "string",
                            "description": "角色的产出数据，JSON 格式字符串。"
                                           "例如分析师的产出：{\"scene_segments\":[...],\"transcript\":\"...\"}",
                        },
                    },
                    "required": ["role_name", "data_json"],
                },
            ),
            ToolDef(
                name="load_latest_context",
                description="加载上下文链中最新的角色产出。派新任务前调用，拿到上一个角色的产出作为上下文。"
                           "不指定序号，永远取最新。返回时附带编号和角色名。",
                fn=_load_latest_context,
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            ToolDef(
                name="load_context_by_index",
                description="按序号加载指定角色的上下文。ctx_001 是第1个角色，ctx_002 是第2个，以此类推。"
                           "如果你知道想要哪个序号，用这个工具。返回时附带角色名。",
                fn=_load_context_by_index,
                parameters={
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "序号，从 1 开始。ctx_001 → index=1",
                        },
                    },
                    "required": ["index"],
                },
            ),
            ToolDef(
                name="show_context_chain",
                description="查看当前所有已保存的上下文链（编号 + 角色 + 时间），了解信息传导到了哪一步。",
                fn=_show_context_chain,
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            ToolDef(
                name="prospect_material",
                description=("勘探一个素材——用VL采样看几眼,快速判断类型和价值."
                           "返回:素材类型/主要内容/画面质量/使用建议."
                           "开工前第一个调用的工具,用来决定这个素材值不值得做."),
                fn=_prospect_material,
                parameters={
                    "type": "object",
                    "properties": {
                        "video_path": {
                            "type": "string",
                            "description": "素材文件的绝对路径",
                        },
                    },
                    "required": ["video_path"],
                },
            ),
            ToolDef(
                name="compression_status",
                description="检查视频压缩进度.压缩完成后才能用 prospect_material/watch_video 等画面工具.",
                fn=_compression_status,
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            ToolDef(
                name="start_new_project",
                description="【新项目】创建全新项目——建独立文件夹,清旧状态."
                           "当你发现用户发来全新的素材、或话题和当前项目无关时调用."
                           "项目名由你根据内容起,简短描述性名称。",
                fn=_start_new_project,
                parameters={
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "项目名,简短描述性名称,系统用它建文件夹。",
                        },
                        "video_paths": {
                            "type": "string",
                            "description": "逗号分隔的素材文件绝对路径,如"
                                           "'C:/videos/demo1.mp4,C:/videos/demo2.mp4'",
                        },
                        "task": {
                            "type": "string",
                            "description": "用户的任务描述(可选)",
                        },
                    },
                    "required": ["project_name", "video_paths"],
                },
            ),
            ToolDef(
                name="audio_prospect",
                description="语音勘探素材——不等压缩完成,直接用原始音频采样.判断:音频类型/内容/情绪/背景音/价值",
                fn=_audio_prospect,
                parameters={
                    "type": "object",
                    "properties": {
                        "video_path": {
                            "type": "string",
                            "description": "素材文件的绝对路径(原始文件即可,无需等压缩)",
                        },
                    },
                    "required": ["video_path"],
                },
            ),
        ]
        return tools
