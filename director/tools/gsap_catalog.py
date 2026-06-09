"""
GSAP 情绪映射 + 动画模板系统
==============================
AI Agent 工具集:查情绪映射 -> 选动画模板 -> 装配 timeline -> 生成代码.

设计原则:
1. 情绪映射表是结构化数据,不是硬编码在 prompt 中
2. 动画模板是预定义的 GSAP 代码片段 + 参数声明
3. timeline 装配工具让 AI 组合多个步骤
"""
import json
from typing import Optional

from director.registry import tool


# ═══════════════════════════════════════════════════════════════
#  情绪->动效映射表(核心)
# ═══════════════════════════════════════════════════════════════

EMOTION_MAP = {
    "surprise": {
        "name": "惊讶/惊喜",
        "description": "用于产品惊艳亮相,'哇塞'时刻,数据突破",
        "animation": {"method": "to", "ease": "back.out(2)", "duration": 0.6, "props": {"scale": 1.3}},
        "variants": [
            {"name": "放大强调", "ease": "back.out(2)", "props": {"scale": 1.3, "opacity": 1}},
            {"name": "弹性弹出", "ease": "elastic.out(1,0.3)", "props": {"scale": 1.2}},
            {"name": "旋转弹出", "ease": "back.out(1.7)", "props": {"rotation": "360_cw", "scale": 1}},
            {"name": "跳跃入场", "ease": "bounce.out", "props": {"y": -30, "opacity": 1}},
        ],
        "examples": ["惊喜开箱", "超低价格展示", "意外反转"],
    },
    "warmth": {
        "name": "温暖/感动",
        "description": "用于亲情,友情,温馨场景,感人故事",
        "animation": {"method": "to", "ease": "sine.inOut", "duration": 0.8, "props": {"opacity": 1, "y": 0}},
        "variants": [
            {"name": "柔和浮现", "ease": "sine.inOut", "props": {"opacity": 1, "y": 0}},
            {"name": "轻柔放大", "ease": "sine.out", "props": {"scale": 1.05, "opacity": 1}},
            {"name": "暖光渗透", "ease": "power2.out", "props": {"opacity": 1, "color": "#FFE4B5"}},
            {"name": "缓缓上升", "ease": "power1.out", "props": {"y": -20, "opacity": 1}},
        ],
        "examples": ["温暖家庭时光", "感恩时刻", "温馨回忆"],
    },
    "emphasis": {
        "name": "强调/重要",
        "description": "用于关键信息强调,核心卖点,重要数据高亮",
        "animation": {"method": "to", "ease": "power3.out", "duration": 0.4, "props": {"scale": 1.15}},
        "variants": [
            {"name": "呼吸脉冲", "ease": "sine.inOut", "props": {"scale": 1.1, "repeat": -1, "yoyo": True}},
            {"name": "弹性弹跳", "ease": "back.out(3)", "props": {"scale": 1.2}},
            {"name": "颜色闪烁", "ease": "power1.out", "props": {"color": "#FF6B35", "scale": 1.05}},
            {"name": "抖动强调", "ease": "power4.out", "props": {"x": 5, "repeat": 2, "yoyo": True}},
        ],
        "examples": ["价格高亮", "核心卖点展示", "CTA按钮强调"],
    },
    "fun": {
        "name": "活泼/年轻",
        "description": "用于社交内容,搞笑视频,年轻化品牌,活泼氛围",
        "animation": {"method": "to", "ease": "bounce.out", "duration": 0.5, "props": {"scale": 1, "y": 0}},
        "variants": [
            {"name": "弹跳入场", "ease": "bounce.out", "props": {"y": 0, "opacity": 1}},
            {"name": "摇摆招呼", "ease": "sine.inOut", "props": {"rotation": 15, "repeat": 1, "yoyo": True}},
            {"name": "旋转弹入", "ease": "back.out(2)", "props": {"rotation": 720, "scale": 1, "opacity": 1}},
            {"name": "逐字跳跃", "ease": "bounce.out", "props": {"y": -20, "stagger": 0.08}},
        ],
        "examples": ["搞笑字幕", "活泼标题", "年轻品牌展示"],
    },
    "premium": {
        "name": "高端/霸气",
        "description": "用于奢侈品,高端品牌,震撼开场,旗舰产品",
        "animation": {"method": "to", "ease": "power4.out", "duration": 1.0, "props": {"opacity": 1, "y": 0}},
        "variants": [
            {"name": "从容展开", "ease": "power4.out", "props": {"opacity": 1, "y": 0, "scale": 1}},
            {"name": "金色辉光", "ease": "sine.out", "props": {"opacity": 1, "textShadow": "0 0 30px rgba(255,215,0,0.5)"}},
            {"name": "慢镜缩放", "ease": "power2.out", "props": {"scale": 1.2, "duration": 1.5}},
            {"name": "威严升起", "ease": "expo.out", "props": {"y": -50, "opacity": 1, "duration": 1.2}},
        ],
        "examples": ["豪车发布", "珠宝展示", "品牌大片开场"],
    },
    "smooth": {
        "name": "流畅/顺滑",
        "description": "用于转场过渡,滑动展示,产品操作演示,时间线推进",
        "animation": {"method": "to", "ease": "power2.inOut", "duration": 0.7, "props": {"x": 0, "opacity": 1}},
        "variants": [
            {"name": "平滑滑动", "ease": "power2.inOut", "props": {"x": 0, "opacity": 1}},
            {"name": "淡入上滑", "ease": "power2.out", "props": {"y": -30, "opacity": 1}},
            {"name": "擦除显现", "ease": "power3.inOut", "props": {"clipPath": "inset(0 0% 0 0)"}},
            {"name": "缩放滑入", "ease": "power1.inOut", "props": {"scale": 1, "x": 0, "opacity": 1}},
        ],
        "examples": ["页面滑动切换", "产品展示滚动", "时间线推进"],
    },
    "conflict": {
        "name": "冲突/反差",
        "description": "用于对比展示,问题揭示,反转前铺垫,戏剧冲突",
        "animation": {"method": "to", "ease": "power3.out", "duration": 0.3, "props": {"x": 10}},
        "variants": [
            {"name": "剧烈抖动", "ease": "power4.out", "props": {"x": 8, "repeat": 3, "yoyo": True, "duration": 0.08}},
            {"name": "碎裂效果", "ease": "back.in(2)", "props": {"scale": 0.1, "opacity": 0, "rotation": 15}},
            {"name": "闪红警告", "ease": "power1.out", "props": {"color": "#FF0000", "opacity": 0.8, "repeat": 2, "yoyo": True}},
            {"name": "缩爆弹出", "ease": "back.out(4)", "props": {"scale": 1.3, "duration": 0.6}},
        ],
        "examples": ["问题揭示", "强烈对比", "剧情反转前奏"],
    },
    "quiet": {
        "name": "安静/深情",
        "description": "用于夜深人静,独白,思考时刻,深情画面",
        "animation": {"method": "to", "ease": "sine.out", "duration": 1.2, "props": {"opacity": 1}},
        "variants": [
            {"name": "极缓浮现", "ease": "sine.out", "props": {"opacity": 1, "duration": 1.5}},
            {"name": "微光闪烁", "ease": "sine.inOut", "props": {"opacity": 0.7, "repeat": -1, "yoyo": True, "duration": 2}},
            {"name": "慢速上浮", "ease": "power1.out", "props": {"y": -10, "opacity": 1, "duration": 1.8}},
            {"name": "温柔膨胀", "ease": "sine.out", "props": {"scale": 1.03, "duration": 2.0}},
        ],
        "examples": ["深夜独白", "深情告白", "安静的自然画面"],
    },
    "cinematic": {
        "name": "电影感",
        "description": "用于电影级开场,史诗感场景,大片质感,片头片尾",
        "animation": {"method": "to", "ease": "power3.inOut", "duration": 1.5, "props": {"opacity": 1, "scale": 1}},
        "variants": [
            {"name": "史诗推近", "ease": "power3.inOut", "props": {"scale": 1.15, "duration": 2.0}},
            {"name": "遮幅展开", "ease": "power4.out", "props": {"height": "100%", "duration": 0.8}},
            {"name": "字幕缓现", "ease": "power2.out", "props": {"opacity": 1, "y": 0, "duration": 1.0}},
            {"name": "胶片滚动", "ease": "none", "props": {"y": -500, "duration": 3.0}},
        ],
        "examples": ["电影开场", "史诗风景", "片尾字幕滚动"],
    },
    "narrative": {
        "name": "叙事/讲述",
        "description": "用于故事讲述,知识讲解,教程步骤,信息传递",
        "animation": {"method": "to", "ease": "power2.out", "duration": 0.6, "props": {"opacity": 1, "y": 0}},
        "variants": [
            {"name": "逐段显现", "ease": "power2.out", "props": {"opacity": 1, "y": 0, "stagger": 0.2}},
            {"name": "打字机效果", "ease": "steps(30)", "props": {"width": "100%", "duration": 1.5}},
            {"name": "强调标记", "ease": "back.out(2)", "props": {"scale": 1.1, "opacity": 1}},
            {"name": "逐步填充", "ease": "power1.inOut", "props": {"width": "100%", "duration": 0.8}},
        ],
        "examples": ["故事讲述", "知识科普", "分步教程"],
    },
}


# ═══════════════════════════════════════════════════════════════
#  GSAP 动画模板库
# ═══════════════════════════════════════════════════════════════

TEMPLATE_LIBRARY = [

    # ── Entrance 入场模板(5个)──────────────────────────────

    {
        "id": "fade_slide_in",
        "name": "淡入上滑入场",
        "category": "entrance",
        "emotions": ["smooth", "warmth", "quiet"],
        "description": "文字从下方 30px 淡入并上滑到正常位置,通用性最强的入场",
        "code_template": "tl.{method}('{selector}', {{y: {y}, opacity: {opacity}, duration: {duration}, ease: '{ease}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".title", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "from", "options": ["to", "from", "fromTo"]},
            {"name": "y", "type": "number", "default": 30, "description": "垂直位移 px"},
            {"name": "opacity", "type": "number", "default": 0, "description": "起始透明度", "min": 0, "max": 1},
            {"name": "duration", "type": "number", "default": 0.6, "min": 0.2, "max": 3},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","power4.out","back.out(1.7)","elastic.out(1,0.3)","bounce.out","sine.inOut","circ.out","expo.out"]},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "scale_in",
        "name": "中心放大入场",
        "category": "entrance",
        "emotions": ["premium", "cinematic", "surprise"],
        "description": "元素从中心由小放大至正常大小,适合标题,Logo 等焦点元素",
        "code_template": "tl.{method}('{selector}', {{scale: {scale}, opacity: {opacity}, duration: {duration}, ease: '{ease}', transformOrigin: '{transformOrigin}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".title", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "from", "options": ["to", "from", "fromTo"]},
            {"name": "scale", "type": "number", "default": 0.3, "description": "起始缩放比例", "min": 0, "max": 2},
            {"name": "opacity", "type": "number", "default": 0, "description": "起始透明度", "min": 0, "max": 1},
            {"name": "duration", "type": "number", "default": 0.7, "min": 0.2, "max": 3},
            {"name": "ease", "type": "enum", "default": "back.out(2)", "options": ["power2.out","back.out(1.7)","back.out(2)","back.out(3)","elastic.out(1,0.3)","expo.out","power4.out"]},
            {"name": "transformOrigin", "type": "string", "default": "center center", "description": "变换原点"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "drop_bounce_in",
        "name": "掉落弹跳入场",
        "category": "entrance",
        "emotions": ["fun", "surprise", "emphasis"],
        "description": "元素从上方掉落并弹跳几次后稳定,活泼有趣",
        "code_template": "tl.{method}('{selector}', {{y: {y}, opacity: {opacity}, duration: {duration}, ease: '{ease}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".item", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "from", "options": ["to", "from", "fromTo"]},
            {"name": "y", "type": "number", "default": -80, "description": "起始垂直偏移 px(负=上方)"},
            {"name": "opacity", "type": "number", "default": 0, "description": "起始透明度", "min": 0, "max": 1},
            {"name": "duration", "type": "number", "default": 0.8, "min": 0.3, "max": 2},
            {"name": "ease", "type": "enum", "default": "bounce.out", "options": ["bounce.out","back.out(2)","elastic.out(1,0.3)","power3.out"]},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "rotate_in",
        "name": "旋转入场",
        "category": "entrance",
        "emotions": ["fun", "surprise", "premium"],
        "description": "元素旋转进入画面,适合品牌 Logo,特殊标题",
        "code_template": "tl.{method}('{selector}', {{rotation: {rotation}, scale: {scale}, opacity: {opacity}, duration: {duration}, ease: '{ease}', transformOrigin: '{transformOrigin}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".logo", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "from", "options": ["to", "from", "fromTo"]},
            {"name": "rotation", "type": "number", "default": 360, "description": "旋转角度(度)"},
            {"name": "scale", "type": "number", "default": 0.1, "description": "起始缩放", "min": 0, "max": 2},
            {"name": "opacity", "type": "number", "default": 0, "description": "起始透明度", "min": 0, "max": 1},
            {"name": "duration", "type": "number", "default": 0.8, "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "back.out(1.7)", "options": ["power3.out","back.out(1.7)","elastic.out(1,0.3)","expo.out","sine.out"]},
            {"name": "transformOrigin", "type": "string", "default": "center center", "description": "变换原点"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "stagger_fade_in",
        "name": "逐元素淡入",
        "category": "entrance",
        "emotions": ["narrative", "smooth", "quiet"],
        "description": "多个元素依次淡入,适合列表,步骤说明,多行文字",
        "code_template": "tl.{method}('{selector}', {{y: {y}, opacity: {opacity}, duration: {duration}, stagger: {stagger}, ease: '{ease}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".item", "description": "CSS 选择器(多个元素)"},
            {"name": "method", "type": "enum", "default": "from", "options": ["to", "from", "fromTo"]},
            {"name": "y", "type": "number", "default": 20, "description": "每项垂直偏移 px"},
            {"name": "opacity", "type": "number", "default": 0, "description": "起始透明度", "min": 0, "max": 1},
            {"name": "duration", "type": "number", "default": 0.5, "min": 0.2, "max": 2},
            {"name": "stagger", "type": "number", "default": 0.15, "description": "每项间隔时间(秒)", "min": 0.02, "max": 1},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","sine.out"]},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },

    # ── Emphasis 强调模板(5个)──────────────────────────────

    {
        "id": "pulse_scale",
        "name": "脉冲缩放",
        "category": "emphasis",
        "emotions": ["emphasis", "surprise", "premium"],
        "description": "元素以呼吸节奏脉冲缩放,吸引注意力但不突兀",
        "code_template": "tl.{method}('{selector}', {{scale: {scale}, duration: {duration}, ease: '{ease}', repeat: {repeat}, yoyo: {yoyo}}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".highlight", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "scale", "type": "number", "default": 1.1, "description": "目标缩放", "min": 1, "max": 2},
            {"name": "duration", "type": "number", "default": 0.6, "min": 0.2, "max": 2},
            {"name": "ease", "type": "enum", "default": "sine.inOut", "options": ["sine.inOut","power1.inOut","power2.inOut"]},
            {"name": "repeat", "type": "number", "default": -1, "description": "重复次数(-1=无限)"},
            {"name": "yoyo", "type": "boolean", "default": True, "description": "是否往返"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "elastic_pop",
        "name": "弹性弹出强调",
        "category": "emphasis",
        "emotions": ["surprise", "fun", "emphasis"],
        "description": "元素弹性缩放突出,适合惊喜弹出,关键数字展示",
        "code_template": "tl.{method}('{selector}', {{scale: {scale}, duration: {duration}, ease: '{ease}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".number", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "scale", "type": "number", "default": 1.3, "description": "目标缩放", "min": 1, "max": 3},
            {"name": "duration", "type": "number", "default": 0.5, "min": 0.2, "max": 1.5},
            {"name": "ease", "type": "enum", "default": "elastic.out(1,0.3)", "options": ["elastic.out(1,0.3)","back.out(3)","back.out(4)","bounce.out"]},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "underline_reveal",
        "name": "下划线展开强调",
        "category": "emphasis",
        "emotions": ["narrative", "emphasis", "cinematic"],
        "description": "下划线从左到右展开,配合文字强调,有书写感",
        "code_template": "tl.{method}('{selector}', {{scaleX: {scaleX}, transformOrigin: '{transformOrigin}', duration: {duration}, ease: '{ease}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".underline", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "from", "options": ["to", "from", "fromTo"]},
            {"name": "scaleX", "type": "number", "default": 0, "description": "起始横向缩放"},
            {"name": "transformOrigin", "type": "string", "default": "left center", "description": "变换原点"},
            {"name": "duration", "type": "number", "default": 0.4, "min": 0.2, "max": 1.5},
            {"name": "ease", "type": "enum", "default": "power3.out", "options": ["power2.out","power3.out","power4.out","expo.out","sine.out"]},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "color_flash",
        "name": "颜色闪烁强调",
        "category": "emphasis",
        "emotions": ["conflict", "emphasis", "surprise"],
        "description": "元素颜色闪烁变化,搭配缩放,适合警告,高亮,重点信息",
        "code_template": "tl.{method}('{selector}', {{color: '{color}', scale: {scale}, duration: {duration}, ease: '{ease}', repeat: {repeat}, yoyo: {yoyo}}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".flash-text", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "color", "type": "string", "default": "#FF6B35", "description": "目标颜色"},
            {"name": "scale", "type": "number", "default": 1.05, "description": "目标缩放", "min": 1, "max": 1.5},
            {"name": "duration", "type": "number", "default": 0.3, "min": 0.1, "max": 1},
            {"name": "ease", "type": "enum", "default": "power1.out", "options": ["power1.out","sine.out","power2.out","none"]},
            {"name": "repeat", "type": "number", "default": 2, "description": "重复次数"},
            {"name": "yoyo", "type": "boolean", "default": True, "description": "是否往返"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "shake_attention",
        "name": "抖动吸引注意",
        "category": "emphasis",
        "emotions": ["conflict", "emphasis", "fun"],
        "description": "元素左右快速抖动,模拟'不'的动作或吸引视线",
        "code_template": "tl.{method}('{selector}', {{x: {x}, duration: {duration}, ease: '{ease}', repeat: {repeat}, yoyo: {yoyo}}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".shake-target", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "x", "type": "number", "default": 8, "description": "抖动幅度 px"},
            {"name": "duration", "type": "number", "default": 0.08, "min": 0.03, "max": 0.3, "description": "单次抖动时长"},
            {"name": "ease", "type": "enum", "default": "none", "options": ["none","power1.out"]},
            {"name": "repeat", "type": "number", "default": 3, "description": "抖动次数"},
            {"name": "yoyo", "type": "boolean", "default": True, "description": "是否往返"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },

    # ── Exit 退场模板(3个)──────────────────────────────────

    {
        "id": "fade_slide_out",
        "name": "淡入下滑退场",
        "category": "exit",
        "emotions": ["smooth", "warmth", "quiet"],
        "description": "元素向下滑动并淡出,平滑自然地离开画面",
        "code_template": "tl.{method}('{selector}', {{y: {y}, opacity: {opacity}, duration: {duration}, ease: '{ease}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".title", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "y", "type": "number", "default": 40, "description": "垂直位移 px(正=向下)"},
            {"name": "opacity", "type": "number", "default": 0, "description": "目标透明度"},
            {"name": "duration", "type": "number", "default": 0.5, "min": 0.2, "max": 2},
            {"name": "ease", "type": "enum", "default": "power2.in", "options": ["power1.in","power2.in","power3.in","sine.in"]},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "scale_out",
        "name": "缩小消失退场",
        "category": "exit",
        "emotions": ["cinematic", "premium", "smooth"],
        "description": "元素缩小并向中心消失,有聚焦到下一场景的感觉",
        "code_template": "tl.{method}('{selector}', {{scale: {scale}, opacity: {opacity}, duration: {duration}, ease: '{ease}', transformOrigin: '{transformOrigin}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".element", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "scale", "type": "number", "default": 0.1, "description": "目标缩放", "min": 0, "max": 1},
            {"name": "opacity", "type": "number", "default": 0, "description": "目标透明度"},
            {"name": "duration", "type": "number", "default": 0.5, "min": 0.2, "max": 2},
            {"name": "ease", "type": "enum", "default": "power3.in", "options": ["power2.in","power3.in","back.in(2)","sine.in"]},
            {"name": "transformOrigin", "type": "string", "default": "center center", "description": "变换原点"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "rotate_out",
        "name": "旋转消失退场",
        "category": "exit",
        "emotions": ["fun", "surprise", "conflict"],
        "description": "元素旋转并缩小消失,活泼有趣带离场感",
        "code_template": "tl.{method}('{selector}', {{rotation: {rotation}, scale: {scale}, opacity: {opacity}, duration: {duration}, ease: '{ease}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".element", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "rotation", "type": "number", "default": 180, "description": "旋转角度(度)"},
            {"name": "scale", "type": "number", "default": 0, "description": "目标缩放"},
            {"name": "opacity", "type": "number", "default": 0, "description": "目标透明度"},
            {"name": "duration", "type": "number", "default": 0.5, "min": 0.2, "max": 2},
            {"name": "ease", "type": "enum", "default": "back.in(1.7)", "options": ["power3.in","back.in(1.7)","back.in(2)","sine.in"]},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },

    # ── Loop 循环模板(3个)────────────────────────────────

    {
        "id": "gentle_float",
        "name": "轻柔浮动",
        "category": "loop",
        "emotions": ["quiet", "warmth", "smooth"],
        "description": "元素持续上下轻柔浮动,营造宁静,梦幻的氛围",
        "code_template": "tl.{method}('{selector}', {{y: {y}, duration: {duration}, ease: '{ease}', repeat: {repeat}, yoyo: {yoyo}}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".float-element", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "y", "type": "number", "default": -15, "description": "浮动偏移 px"},
            {"name": "duration", "type": "number", "default": 2.5, "min": 1, "max": 6},
            {"name": "ease", "type": "enum", "default": "sine.inOut", "options": ["sine.inOut","power1.inOut"]},
            {"name": "repeat", "type": "number", "default": -1, "description": "重复次数(-1=无限)"},
            {"name": "yoyo", "type": "boolean", "default": True, "description": "是否往返"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "pulse_glow",
        "name": "脉冲呼吸发光",
        "category": "loop",
        "emotions": ["premium", "emphasis", "cinematic"],
        "description": "元素呼吸式明暗脉动 + 发光感,适合按钮,Logo,光环",
        "code_template": "tl.{method}('{selector}', {{opacity: {opacity}, boxShadow: '{boxShadow}', duration: {duration}, ease: '{ease}', repeat: {repeat}, yoyo: {yoyo}}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".glow", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "opacity", "type": "number", "default": 0.6, "description": "目标透明度", "min": 0, "max": 1},
            {"name": "boxShadow", "type": "string", "default": "0 0 20px rgba(255,215,0,0.6)", "description": "发光阴影"},
            {"name": "duration", "type": "number", "default": 1.2, "min": 0.4, "max": 4},
            {"name": "ease", "type": "enum", "default": "sine.inOut", "options": ["sine.inOut","power1.inOut"]},
            {"name": "repeat", "type": "number", "default": -1, "description": "重复次数(-1=无限)"},
            {"name": "yoyo", "type": "boolean", "default": True, "description": "是否往返"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
    {
        "id": "swing_attention",
        "name": "轻微摆动",
        "category": "loop",
        "emotions": ["fun", "emphasis", "warmth"],
        "description": "元素左右轻微摆动,模拟招手或钟摆效果,吸引视线",
        "code_template": "tl.{method}('{selector}', {{rotation: {rotation}, duration: {duration}, ease: '{ease}', repeat: {repeat}, yoyo: {yoyo}, transformOrigin: '{transformOrigin}'}}, {position});",
        "variables": [
            {"name": "selector", "type": "string", "default": ".swing", "description": "CSS 选择器"},
            {"name": "method", "type": "enum", "default": "to", "options": ["to", "from", "fromTo"]},
            {"name": "rotation", "type": "number", "default": 10, "description": "摆动角度(度)"},
            {"name": "duration", "type": "number", "default": 1.5, "min": 0.5, "max": 4},
            {"name": "ease", "type": "enum", "default": "sine.inOut", "options": ["sine.inOut","power1.inOut"]},
            {"name": "repeat", "type": "number", "default": -1, "description": "重复次数(-1=无限)"},
            {"name": "yoyo", "type": "boolean", "default": True, "description": "是否往返"},
            {"name": "transformOrigin", "type": "string", "default": "center bottom", "description": "变换原点"},
            {"name": "position", "type": "number", "default": 0, "description": "timeline 位置(秒)"},
        ],
    },
]

# 构建模板索引
_TEMPLATE_INDEX: dict[str, dict] = {t["id"]: t for t in TEMPLATE_LIBRARY}


# ═══════════════════════════════════════════════════════════════
#  HTML 模板引擎 — 生成 HF 协议兼容的 GSAP Timeline 页面
# ═══════════════════════════════════════════════════════════════

_HTML_FRAME = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:transparent;overflow:hidden;width:1280px;height:720px}}
#root{{position:relative;width:1280px;height:720px;overflow:hidden;display:flex;align-items:center;justify-content:center;flex-direction:column}}
{extra_css}
</style>
</head>
<body>
<div id="root" data-composition-id="{composition_id}" data-duration="{total_duration}" data-width="1280" data-height="720">
{extra_html}
</div>
<script>
window.__timelines = window.__timelines || {{}};
window.__timelines["{composition_id}"] = gsap.timeline({{paused:true}});
const tl = window.__timelines["{composition_id}"];
{timeline_code}
window.__hf = window.__hf || {{}};
window.__hf.duration = {total_duration};
window.__hf.seek = function(t) {{ window.__timelines["{composition_id}"].seek(t, true); }};
</script>
</body>
</html>"""


def _calc_total_duration(steps: list[dict]) -> float:
    """
    估算 timeline 总时长.
    遍历每个步骤,取 position + duration 的最大值作为总时长.
    """
    if not steps:
        return 0.0
    total = 0.0
    for step in steps:
        vars_ = step.get("variables", {})
        pos = float(vars_.get("position", 0))
        dur = float(vars_.get("duration", 0.6))
        step_end = pos + dur
        if step_end > total:
            total = step_end
    return max(total, 0.5)  # 至少 0.5 秒


def _render_template(template: dict, variables: dict) -> str:
    """
    用变量替换模板中的占位符.
    处理特殊值如 '360_cw'(转为 360 度),处理布尔值.
    """
    code = template["code_template"]
    # 从 code_template 中提取 {var} 占位符并替换
    var_defaults = {v["name"]: v.get("default") for v in template.get("variables", [])}
    merged = {}
    merged.update(var_defaults)
    merged.update(variables)

    # 替换 code_template 中的变量
    result = code
    for v in template.get("variables", []):
        name = v["name"]
        val = merged.get(name, v.get("default"))
        if val is None:
            val = ""
        # 处理布尔值
        if isinstance(val, bool):
            val = "true" if val else "false"
        # 字符串值加引号(仅当它不在代码模板里以 {name} 被手动处理时)
        result = result.replace("{" + name + "}", str(val))

    return result


# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════


@tool(
    name="list_emotion_animations",
    description=(
        "列出情绪->动效映射表.如果不传 emotion,显示所有情绪概览(10 种情绪及核心动画)."
        "如果传入 emotion ID(如 'surprise'),显示该情绪的完整信息:"
        "中文名,核心动画参数,所有变体(variants),使用场景示例."
        "phase=plan,在 AI 编排出具体动画方案前使用,帮助选择合适的情绪基调."
    ),
    phase="plan",
    category="animation",
    tags=["gsap", "emotion", "animation"],
    examples=[
        {"args": {}, "result": "所有 10 种情绪概览"},
        {"args": {"emotion": "surprise"}, "result": "惊讶/惊喜情绪的完整映射与变体"},
    ],
    group="花字与动画(效果层)",
)
def list_emotion_animations(emotion: str = "") -> str:
    """
    列出情绪->动效映射表.

    Args:
        emotion: 情绪 ID,如 "surprise","warmth","emphasis","fun",
                 "premium","smooth","conflict","quiet","cinematic","narrative".
                 不传则显示所有情绪概览.

    Returns:
        格式化的情绪映射信息
    """
    if emotion:
        emotion = emotion.strip().lower()
        if emotion not in EMOTION_MAP:
            available = ",".join(EMOTION_MAP.keys())
            return f"未知情绪: '{emotion}'.可用情绪: {available}"

        em = EMOTION_MAP[emotion]
        lines = [
            f"## {em['name']} [{emotion}]",
            f"  描述:{em['description']}",
            f"  核心:gsap.{em['animation']['method']}(el, {json.dumps(em['animation']['props'], ensure_ascii=False)})",
            f"         ease: {em['animation']['ease']}, duration: {em['animation']['duration']}",
            "",
            "  变体:",
        ]
        for v in em["variants"]:
            props_str = json.dumps(v["props"], ensure_ascii=False)
            lines.append(f"    - {v['name']}: ease={v['ease']}, {props_str}")
        lines.append("")
        lines.append(f"  场景:{','.join(em['examples'])}")
        return "\n".join(lines)

    # 显示所有情绪概览
    lines = [
        "## 情绪->动效映射表",
        "",
    ]
    for eid, em in EMOTION_MAP.items():
        anim = em["animation"]
        props_short = ", ".join(f"{k}={v}" for k, v in anim["props"].items())
        variant_names = ",".join(v["name"] for v in em["variants"])
        lines.append(f"{em['name']} [{eid}]")
        lines.append(f"  核心:gsap.{anim['method']}(el, {{{props_short}}}, ease={anim['ease']})")
        lines.append(f"  变体:{variant_names}")
        lines.append(f"  场景:{','.join(em['examples'])}")
        lines.append("")
    return "\n".join(lines)


@tool(
    name="search_animation",
    description=(
        "按情绪/分类/关键词搜索 GSAP 动画模板."
        "支持按情绪(如 surprise),分类(entrance/emphasis/exit/loop),或关键词搜索."
        "可以组合多个条件.返回匹配的模板简要列表(id,名称,分类,适配情绪)."
        "phase=plan,在选好情绪基调后,搜索具体可用的动画模板."
    ),
    phase="plan",
    category="animation",
    tags=["gsap", "search", "template"],
    examples=[
        {"args": {"emotion": "surprise"}, "result": "适用于 surprise 情绪的模板列表"},
        {"args": {"category": "entrance"}, "result": "所有入场模板列表"},
        {"args": {"keyword": "旋转"}, "result": "包含'旋转'关键词的模板"},
    ],
    group="花字与动画(效果层)",
)
def search_animation(
    emotion: str = "",
    category: str = "",
    keyword: str = "",
) -> str:
    """
    按情绪/分类/关键词搜索动画模板.

    Args:
        emotion: 情绪 ID(可选),如 "surprise","warmth"
        category: 分类(可选),"entrance" / "emphasis" / "exit" / "loop"
        keyword: 关键词(可选),匹配模板名称和描述

    Returns:
        匹配的模板列表
    """
    results = TEMPLATE_LIBRARY[:]

    # 情绪筛选
    if emotion:
        emotion = emotion.strip().lower()
        results = [t for t in results if emotion in t["emotions"]]

    # 分类筛选
    if category:
        category = category.strip().lower()
        results = [t for t in results if t["category"] == category]

    # 关键词筛选
    if keyword:
        keyword = keyword.strip().lower()
        results = [
            t for t in results
            if keyword in t["name"].lower()
            or keyword in t["description"].lower()
            or keyword in t["id"].lower()
        ]

    if not results:
        filters = []
        if emotion:
            filters.append(f"情绪={emotion}")
        if category:
            filters.append(f"分类={category}")
        if keyword:
            filters.append(f"关键词={keyword}")
        filter_str = " ".join(filters)
        return f"未找到匹配的动画模板({filter_str})"

    lines = ["## 搜索结果", ""]
    for t in results:
        emotions_str = ",".join(
            f"{EMOTION_MAP.get(e, {}).get('name', e)}[{e}]"
            for e in t["emotions"]
        )
        lines.append(f"### {t['name']} ({t['id']})")
        lines.append(f"  分类:{t['category']}")
        lines.append(f"  适配情绪:{emotions_str}")
        lines.append(f"  描述:{t['description']}")
        lines.append(f"  参数:{len(t.get('variables', []))} 个可调参数")
        lines.append("")
    lines.append(f"共 {len(results)} 个结果")
    return "\n".join(lines)


@tool(
    name="get_animation_template",
    description=(
        "获取单个 GSAP 动画模板的完整信息."
        "包括模板 ID,名称,分类,适配情绪列表,详细描述,"
        "GSAP 代码模板(含 {placeholder}),所有可调变量(名称/类型/默认值/描述/范围),"
        "以及自动生成的 JS 代码示例."
        "phase=plan,在确定模板后查看完整参数细节以准备拼装 timeline."
    ),
    phase="plan",
    category="animation",
    tags=["gsap", "template", "detail"],
    examples=[
        {"args": {"template_id": "fade_slide_in"}, "result": "fade_slide_in 模板的完整信息"},
    ],
    group="花字与动画(效果层)",
)
def get_animation_template(template_id: str) -> str:
    """
    获取单个模板的完整信息 + 用法示例.

    Args:
        template_id: 模板 ID,如 "fade_slide_in","scale_in","pulse_scale"

    Returns:
        完整的模板信息
    """
    template = _TEMPLATE_INDEX.get(template_id)
    if not template:
        available = ",".join(t["id"] for t in TEMPLATE_LIBRARY)
        return f"未知模板 ID: '{template_id}'.可用模板: {available}"

    lines = [
        f"## {template['name']} ({template['id']})",
        f"  分类:{template['category']}",
        f"  适配情绪:{','.join(template['emotions'])}",
        f"  描述:{template['description']}",
        "",
        "### GSAP 代码模板",
        f"  ```javascript",
        f"  {template['code_template']}",
        f"  ```",
        "",
        "### 可调变量",
    ]
    for v in template["variables"]:
        vtype = v.get("type", "string")
        default = v.get("default", "—")
        desc = v.get("description", "")
        constraints = []
        if "min" in v:
            constraints.append(f"min={v['min']}")
        if "max" in v:
            constraints.append(f"max={v['max']}")
        if "options" in v:
            constraints.append(f"可选值: {'|'.join(str(o) for o in v['options'][:5])}...")
        constraint_str = " (" + ", ".join(constraints) + ")" if constraints else ""
        lines.append(f"  - {v['name']} ({vtype}) = {default}{constraint_str}")
        lines.append(f"    {desc}")

    # 生成使用示例
    var_defaults = {v["name"]: v.get("default") for v in template.get("variables", [])}
    example_code = _render_template(template, var_defaults)
    lines.append("")
    lines.append("### 用法示例(默认参数)")
    lines.append(f"  ```javascript")
    lines.append(f"  {example_code}")
    lines.append(f"  ```")

    return "\n".join(lines)


@tool(
    name="build_gsap_timeline",
    description=(
        "将多个 GSAP 动画步骤装配成一个完整的 GSAP Timeline HTML 页面."
        "输入 steps 列表,每个 step 包含 template_id,selector,variables."
        "输出完整的可用 HTML 文件路径,这个 HTML:"
        "1. 引用了 GSAP CDN"
        "2. 创建 paused timeline 并注册到 window.__timelines"
        "3. 实现了 window.__hf 协议(duration + seek)"
        "4. 可用 hf_snapshot 直接截图验证"
        "phase=edit,在完成所有动画步骤设计后,最终生成可预览的 HTML."
    ),
    phase="edit",
    category="animation",
    tags=["gsap", "timeline", "build", "html"],
    examples=[
        {
            "args": {
                "steps": json.dumps([
                    {"template_id": "fade_slide_in", "selector": "#title", "variables": {"y": 40, "duration": 0.8, "ease": "power3.out"}},
                    {"template_id": "stagger_fade_in", "selector": ".bullet", "variables": {"duration": 0.5, "stagger": 0.15}},
                ]),
            },
            "result": "生成的 HTML 文件路径",
        },
    ],
    group="花字与动画(效果层)",
)
def build_gsap_timeline(
    steps: str,
    composition_id: str = "main",
) -> str:
    """
    接收步骤列表,返回完整的 GSAP timeline HTML 代码.

    Args:
        steps: JSON 字符串或 Python list,每项包含:
            - template_id: 模板 ID(必须)
            - selector: CSS 选择器(必须)
            - variables: 变量字典(可选,自动补默认值)
        composition_id: composition 标识,默认 "main"

    Returns:
        完整的 HTML 代码字符串(可直接写为 .html 文件用 hf_snapshot 截图)
    """
    # 解析 steps
    if isinstance(steps, str):
        try:
            steps_list = json.loads(steps)
        except (json.JSONDecodeError, TypeError):
            return f"❌ steps 解析失败:无效的 JSON 格式"
    elif isinstance(steps, list):
        steps_list = steps
    else:
        return f"❌ steps 类型错误:期望 JSON 字符串或列表,收到 {type(steps).__name__}"

    if not steps_list or not isinstance(steps_list, list):
        return "❌ steps 为空或无效:请提供至少一个动画步骤"

    # 校验每个 step
    timeline_code_lines = []
    css_parts = []
    html_elements = []
    errors = []

    for i, step in enumerate(steps_list):
        template_id = step.get("template_id", "")
        selector = step.get("selector", "")
        variables = step.get("variables", {})

        if not template_id:
            errors.append(f"步骤[{i}] 缺少 template_id")
            continue
        if not selector:
            errors.append(f"步骤[{i}] 缺少 selector")
            continue

        template = _TEMPLATE_INDEX.get(template_id)
        if not template:
            errors.append(f"步骤[{i}] 无效的 template_id: '{template_id}'")
            continue

        # 合并变量(变量优先覆盖默认值)
        merged_vars = {}
        for v in template.get("variables", []):
            merged_vars[v["name"]] = v.get("default")
        merged_vars["selector"] = selector
        merged_vars.update(variables)

        # 渲染代码
        try:
            code = _render_template(template, merged_vars)
            timeline_code_lines.append(f"tl{code}")
        except Exception as e:
            errors.append(f"步骤[{i}] 渲染失败: {e}")

    if errors:
        error_detail = "\n".join(errors)
        if not timeline_code_lines:
            return f"❌ Timeline 构建失败:\n{error_detail}"

    # 计算总时长
    total_duration = _calc_total_duration(steps_list)
    total_duration = max(total_duration, 0.5)

    # 组装完整 HTML
    extra_css = "\n".join(css_parts) if css_parts else ""
    extra_html = "\n".join(html_elements) if html_elements else ""
    timeline_code = "\n".join(timeline_code_lines)

    if errors:
        timeline_code = (
            f"// ⚠️ 部分步骤有错误:\n// " + "\n// ".join(errors) + "\n"
            + timeline_code
        )

    html = _HTML_FRAME.format(
        composition_id=composition_id,
        total_duration=total_duration,
        extra_css=extra_css,
        extra_html=extra_html,
        timeline_code=timeline_code,
    )

    return html


# ═══════════════════════════════════════════════════════════════
#  辅助函数(供其他模块调用)
# ═══════════════════════════════════════════════════════════════


def get_emotion_map() -> dict:
    """返回完整情绪映射表(供其他 Python 代码使用)"""
    return EMOTION_MAP


def get_template_library() -> list[dict]:
    """返回完整模板库(供其他 Python 代码使用)"""
    return TEMPLATE_LIBRARY


def get_template(template_id: str) -> Optional[dict]:
    """按 ID 获取模板定义"""
    return _TEMPLATE_INDEX.get(template_id)


# 工具已通过 @tool 装饰器自动注册到 Registry
