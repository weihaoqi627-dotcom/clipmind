"""
HF 模板目录系统 — 选模板 -> 填参数 -> 生成 composition -> 预览
==================================================================
AI Agent 通过此工具集访问和管理 HyperFrames 模板.

工作流:
  1. list_hf_templates() -> 浏览可用模板(按分类/情绪/关键词筛选)
  2. get_hf_template_schema() -> 查看某个模板的完整参数定义
  3. generate_hf_composition() -> 选模板 + 填参数 -> 生成 composition HTML
  4. preview_hf_template() -> 生成 composition 并截图第一帧

所有模板引用 hf_engine/templates/ 中的现有实现,此处仅做目录管理和参数编排.
"""

import json
import os
import subprocess
import hashlib
import re
from pathlib import Path
from typing import Optional
from datetime import datetime

from director.registry import tool
from director.config import PROJECT_ROOT, DRAFTS_DIR as _DRAFTS_DIR, DATA_DIR

_PROJECT_DIR = Path(__file__).parent.parent.parent
_HF_TEMPLATES_DIR = _PROJECT_DIR / "hf_engine" / "templates"
# 可写数据根（打包模式下由 CLIPMIND_DATA_HOME 控制）
_DATA_ROOT = DATA_DIR.parent


# ===== 模板目录 =====

_TEMPLATE_CATALOG = [

    # -- entrance(入场动效)--

    {
        "id": "slide_up_text",
        "name": "文字上滑入场",
        "category": "entrance",
        "emotion_tags": ["smooth", "warmth"],
        "description": "文字从下方滑入并淡出到正常位置,适合正文内容出现",
        "variables": [
            {"name": "text", "type": "string", "default": "Hello", "description": "显示的文字内容", "maxLength": 100},
            {"name": "font_size", "type": "number", "default": 64, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 0.8, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power3.out", "options": ["power1.out","power2.out","power3.out","power4.out","back.out(1.7)","expo.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
        ],
    },
    {
        "id": "scale_in_text",
        "name": "中心放大入场",
        "category": "entrance",
        "emotion_tags": ["emphasis", "surprise"],
        "description": "文字从中心由小放大出现,适合标题/关键词强调",
        "variables": [
            {"name": "text", "type": "string", "default": "WOW", "description": "显示的文字内容", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 80, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 0.6, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "back.out(1.7)", "options": ["power1.out","power2.out","power3.out","power4.out","back.out(1.7)","elastic.out(1,0.5)","expo.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
            {"name": "scale_from", "type": "number", "default": 0, "description": "起始缩放比例(0=完全透明缩小)", "min": 0, "max": 1},
        ],
    },
    {
        "id": "typewriter",
        "name": "打字机效果",
        "category": "entrance",
        "emotion_tags": ["quiet", "narrative"],
        "description": "文字像打字机一样逐字出现,适合故事叙述或信件内容",
        "variables": [
            {"name": "text", "type": "string", "default": "Once upon a time...", "description": "显示的文字内容", "maxLength": 200},
            {"name": "font_size", "type": "number", "default": 48, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 2.0, "description": "动画总时长(秒)", "min": 0.5, "max": 10},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
            {"name": "cursor_color", "type": "color", "default": "#FFFFFF", "description": "光标颜色"},
            {"name": "cursor_blink", "type": "boolean", "default": True, "description": "是否显示光标闪烁"},
        ],
    },
    {
        "id": "fade_in_text",
        "name": "文字淡入",
        "category": "entrance",
        "emotion_tags": ["quiet", "warmth"],
        "description": "文字从透明淡入到完全可见,最简单通用的入场方式",
        "variables": [
            {"name": "text", "type": "string", "default": "Welcome", "description": "显示的文字内容", "maxLength": 100},
            {"name": "font_size", "type": "number", "default": 64, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 1.0, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","power4.out","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
        ],
    },
    {
        "id": "drop_in_text",
        "name": "文字掉落弹跳",
        "category": "entrance",
        "emotion_tags": ["fun", "bounce"],
        "description": "文字从上方掉落并带有弹跳效果,活泼有趣",
        "variables": [
            {"name": "text", "type": "string", "default": "BOOM!", "description": "显示的文字内容", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 72, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 1.2, "description": "动画时长(秒)", "min": 0.5, "max": 3},
            {"name": "ease", "type": "enum", "default": "bounce.out", "options": ["bounce.out","elastic.out(1,0.3)","back.out(2)","power2.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
            {"name": "bounce", "type": "boolean", "default": True, "description": "是否启用弹跳效果"},
            {"name": "drop_height", "type": "number", "default": 200, "description": "掉落起始高度(px)", "min": 50, "max": 500},
        ],
    },

    # -- emphasis(强调动效)--

    {
        "id": "pulse_scale",
        "name": "脉冲缩放",
        "category": "emphasis",
        "emotion_tags": ["emphasis", "surprise"],
        "description": "文字脉冲式放大再恢复,适合强调关键词或重要信息",
        "variables": [
            {"name": "text", "type": "string", "default": "重要!", "description": "显示的文字内容", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 72, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 0.8, "description": "每次脉冲时长(秒)", "min": 0.3, "max": 3},
            {"name": "scale_min", "type": "number", "default": 1, "description": "最小缩放比例", "min": 0.5, "max": 2},
            {"name": "scale_max", "type": "number", "default": 1.3, "description": "最大缩放比例", "min": 1, "max": 3},
            {"name": "pulses", "type": "number", "default": 2, "description": "脉冲次数", "min": 1, "max": 10},
            {"name": "ease", "type": "enum", "default": "power2.inOut", "options": ["power1.inOut","power2.inOut","power3.inOut","linear"], "description": "缓动函数,控制动画速度曲线"},
        ],
    },
    {
        "id": "elastic_highlight",
        "name": "弹性高亮",
        "category": "emphasis",
        "emotion_tags": ["surprise", "fun"],
        "description": "文字或背景弹性伸缩高亮,吸引注意力",
        "variables": [
            {"name": "text", "type": "string", "default": "Highlight", "description": "显示的文字内容", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 64, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "highlight_color", "type": "color", "default": "#FFD700", "description": "高亮背景颜色"},
            {"name": "duration", "type": "number", "default": 1.0, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "elastic.out(1,0.3)", "options": ["elastic.out(1,0.3)","elastic.out(1,0.5)","back.out(2)","power3.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
        ],
    },
    {
        "id": "underline_reveal",
        "name": "下划线展开",
        "category": "emphasis",
        "emotion_tags": ["emphasis", "premium"],
        "description": "文字下划线从左到右展开,优雅强调重点",
        "variables": [
            {"name": "text", "type": "string", "default": "重点内容", "description": "显示的文字内容", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 64, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "underline_color", "type": "color", "default": "#FF6B6B", "description": "下划线颜色"},
            {"name": "duration", "type": "number", "default": 0.6, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power3.out", "options": ["power1.out","power2.out","power3.out","power4.out","expo.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
            {"name": "underline_height", "type": "number", "default": 4, "description": "下划线高度(px)", "min": 2, "max": 20},
        ],
    },
    {
        "id": "glow_highlight",
        "name": "发光高亮",
        "category": "emphasis",
        "emotion_tags": ["premium", "emphasis"],
        "description": "文字发光高亮效果,适合科技感或高品质场景",
        "variables": [
            {"name": "text", "type": "string", "default": "Premium", "description": "显示的文字内容", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 72, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "glow_color", "type": "color", "default": "#00BFFF", "description": "发光颜色"},
            {"name": "glow_intensity", "type": "number", "default": 20, "description": "发光强度(px)", "min": 5, "max": 60},
            {"name": "duration", "type": "number", "default": 1.0, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
        ],
    },
    {
        "id": "color_pop",
        "name": "颜色突变",
        "category": "emphasis",
        "emotion_tags": ["conflict", "surprise"],
        "description": "文字颜色突然变化,制造视觉冲击和意外感",
        "variables": [
            {"name": "text", "type": "string", "default": "SURPRISE!", "description": "显示的文字内容", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 80, "description": "字号 px", "min": 24, "max": 200},
            {"name": "original_color", "type": "color", "default": "#FFFFFF", "description": "初始文字颜色"},
            {"name": "pop_color", "type": "color", "default": "#FF4500", "description": "突变后的颜色"},
            {"name": "duration", "type": "number", "default": 0.4, "description": "颜色突变时长(秒)", "min": 0.1, "max": 2},
            {"name": "ease", "type": "enum", "default": "power2.inOut", "options": ["power1.inOut","power2.inOut","linear","steps(2)","steps(4)"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
        ],
    },

    # -- exit(退场动效)--

    {
        "id": "fade_out_text",
        "name": "文字淡出",
        "category": "exit",
        "emotion_tags": ["quiet", "smooth"],
        "description": "文字渐隐消失,最简单的退场方式",
        "variables": [
            {"name": "text", "type": "string", "default": "See you", "description": "显示的文字内容", "maxLength": 100},
            {"name": "font_size", "type": "number", "default": 64, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 0.8, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power2.in", "options": ["power1.in","power2.in","power3.in","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
        ],
    },
    {
        "id": "slide_down_text",
        "name": "文字下滑退场",
        "category": "exit",
        "emotion_tags": ["smooth"],
        "description": "文字向下滑出消失,与上滑入场对应",
        "variables": [
            {"name": "text", "type": "string", "default": "再见", "description": "显示的文字内容", "maxLength": 100},
            {"name": "font_size", "type": "number", "default": 64, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 0.6, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power2.in", "options": ["power1.in","power2.in","power3.in","power4.in"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
        ],
    },
    {
        "id": "scale_out_text",
        "name": "缩小消失",
        "category": "exit",
        "emotion_tags": ["emphasis"],
        "description": "文字缩小并消失,带有一点强调感的退场",
        "variables": [
            {"name": "text", "type": "string", "default": "The End", "description": "显示的文字内容", "maxLength": 100},
            {"name": "font_size", "type": "number", "default": 72, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 0.5, "description": "动画时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power3.in", "options": ["power1.in","power2.in","power3.in","back.in(1.7)","expo.in"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
            {"name": "scale_to", "type": "number", "default": 0, "description": "缩小的目标比例(0=完全消失)", "min": 0, "max": 1},
        ],
    },

    # -- transition(转场)--

    {
        "id": "crossfade",
        "name": "交叉淡入淡出",
        "category": "transition",
        "emotion_tags": ["smooth", "quiet"],
        "description": "画面前后交叉淡入淡出,最通用的转场效果",
        "variables": [
            {"name": "duration", "type": "number", "default": 0.5, "description": "转场时长(秒)", "min": 0.2, "max": 3},
            {"name": "ease", "type": "enum", "default": "power2.inOut", "options": ["power1.inOut","power2.inOut","power3.inOut","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 2},
            {"name": "bg_color", "type": "color", "default": "#000000", "description": "过渡背景颜色"},
        ],
    },
    {
        "id": "slide_left",
        "name": "左滑切换",
        "category": "transition",
        "emotion_tags": ["smooth"],
        "description": "画面向左滑动切换,标准单方向转场",
        "variables": [
            {"name": "duration", "type": "number", "default": 0.6, "description": "转场时长(秒)", "min": 0.2, "max": 3},
            {"name": "ease", "type": "enum", "default": "power3.inOut", "options": ["power1.inOut","power2.inOut","power3.inOut","power4.inOut"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 2},
            {"name": "bg_color", "type": "color", "default": "#000000", "description": "背景颜色"},
        ],
    },
    {
        "id": "slide_right",
        "name": "右滑切换",
        "category": "transition",
        "emotion_tags": ["smooth"],
        "description": "画面向右滑动切换,标准单方向转场",
        "variables": [
            {"name": "duration", "type": "number", "default": 0.6, "description": "转场时长(秒)", "min": 0.2, "max": 3},
            {"name": "ease", "type": "enum", "default": "power3.inOut", "options": ["power1.inOut","power2.inOut","power3.inOut","power4.inOut"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 2},
            {"name": "bg_color", "type": "color", "default": "#000000", "description": "背景颜色"},
        ],
    },
    {
        "id": "zoom_transition",
        "name": "放大推进转场",
        "category": "transition",
        "emotion_tags": ["emphasis", "premium"],
        "description": "画面放大推进切换,产生向前冲的动感",
        "variables": [
            {"name": "duration", "type": "number", "default": 0.7, "description": "转场时长(秒)", "min": 0.2, "max": 3},
            {"name": "ease", "type": "enum", "default": "power3.inOut", "options": ["power1.inOut","power2.inOut","power3.inOut","power4.inOut","expo.inOut"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 2},
            {"name": "bg_color", "type": "color", "default": "#000000", "description": "背景颜色"},
            {"name": "zoom_factor", "type": "number", "default": 1.5, "description": "放大倍数", "min": 1.1, "max": 5},
        ],
    },
    {
        "id": "blur_transition",
        "name": "模糊过渡",
        "category": "transition",
        "emotion_tags": ["quiet", "warmth"],
        "description": "画面先模糊再清晰,柔和的过渡方式",
        "variables": [
            {"name": "duration", "type": "number", "default": 0.8, "description": "转场时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power2.inOut", "options": ["power1.inOut","power2.inOut","power3.inOut","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 2},
            {"name": "bg_color", "type": "color", "default": "#000000", "description": "背景颜色"},
            {"name": "blur_amount", "type": "number", "default": 20, "description": "最大模糊程度(px)", "min": 5, "max": 100},
        ],
    },
    {
        "id": "glitch_transition",
        "name": "故障转场",
        "category": "transition",
        "emotion_tags": ["conflict"],
        "description": "画面出现数字故障效果再切换,适合科技/赛博风格",
        "variables": [
            {"name": "duration", "type": "number", "default": 0.5, "description": "转场时长(秒)", "min": 0.2, "max": 2},
            {"name": "ease", "type": "enum", "default": "power2.inOut", "options": ["power1.inOut","power2.inOut","steps(5)","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 2},
            {"name": "bg_color", "type": "color", "default": "#000000", "description": "背景颜色"},
            {"name": "glitch_intensity", "type": "number", "default": 10, "description": "故障块强度(px)", "min": 3, "max": 50},
            {"name": "rgb_split", "type": "boolean", "default": True, "description": "是否启用 RGB 通道分离"},
        ],
    },
    {
        "id": "light_leak",
        "name": "漏光转场",
        "category": "transition",
        "emotion_tags": ["premium", "cinematic"],
        "description": "模拟胶片漏光效果的转场,电影感十足",
        "variables": [
            {"name": "duration", "type": "number", "default": 1.0, "description": "转场时长(秒)", "min": 0.3, "max": 3},
            {"name": "ease", "type": "enum", "default": "power2.inOut", "options": ["power1.inOut","power2.inOut","power3.inOut","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 2},
            {"name": "leak_color", "type": "color", "default": "#FFD700", "description": "漏光颜色"},
            {"name": "leak_position", "type": "enum", "default": "top_right", "options": ["top_left","top_right","bottom_left","bottom_right","center","random"], "description": "漏光出现位置"},
            {"name": "leak_intensity", "type": "number", "default": 0.6, "description": "漏光强度", "min": 0.1, "max": 1},
        ],
    },

    # -- overlay(叠层)--

    {
        "id": "lower_third",
        "name": "下三分之一标题",
        "category": "overlay",
        "emotion_tags": ["premium", "narrative"],
        "description": "屏幕下方三分之一处显示标题栏,适合人物介绍/章节标题",
        "variables": [
            {"name": "title", "type": "string", "default": "人物名称", "description": "主标题文字", "maxLength": 50},
            {"name": "subtitle", "type": "string", "default": "职位 / 描述", "description": "副标题文字", "maxLength": 80},
            {"name": "font_size", "type": "number", "default": 48, "description": "主标题字号 px", "min": 24, "max": 120},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "accent_color", "type": "color", "default": "#FF6B6B", "description": "强调色(装饰条)"},
            {"name": "duration", "type": "number", "default": 3.0, "description": "显示时长(秒)", "min": 1, "max": 30},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","power4.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "animation", "type": "enum", "default": "slide_in", "options": ["slide_in","fade_in","scale_in","typewriter"], "description": "入场动画类型"},
            {"name": "bg_opacity", "type": "number", "default": 0.7, "description": "背景透明度", "min": 0, "max": 1},
        ],
    },
    {
        "id": "social_handle",
        "name": "社交账号展示",
        "category": "overlay",
        "emotion_tags": ["fun"],
        "description": "展示社交媒体账号,带平台图标动画",
        "variables": [
            {"name": "handle", "type": "string", "default": "@username", "description": "社交账号", "maxLength": 50},
            {"name": "platform", "type": "enum", "default": "twitter", "options": ["twitter","instagram","youtube","tiktok","facebook","linkedin","weibo","bilibili","douyin"], "description": "社交媒体平台类型"},
            {"name": "font_size", "type": "number", "default": 40, "description": "字号 px", "min": 20, "max": 100},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "icon_color", "type": "color", "default": "#1DA1F2", "description": "图标颜色"},
            {"name": "duration", "type": "number", "default": 2.0, "description": "显示时长(秒)", "min": 1, "max": 10},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","back.out(1.7)"], "description": "缓动函数,控制动画速度曲线"},
        ],
    },
    {
        "id": "instagram_follow",
        "name": "Ins 关注动画",
        "category": "overlay",
        "emotion_tags": ["fun"],
        "description": "Instagram 风格关注引导动画,含关注按钮",
        "variables": [
            {"name": "username", "type": "string", "default": "username", "description": "Ins 用户名", "maxLength": 30},
            {"name": "font_size", "type": "number", "default": 36, "description": "字号 px", "min": 20, "max": 80},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "button_color", "type": "color", "default": "#0095F6", "description": "按钮颜色"},
            {"name": "duration", "type": "number", "default": 2.5, "description": "显示时长(秒)", "min": 1, "max": 10},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","back.out(1.7)"], "description": "缓动函数,控制动画速度曲线"},
        ],
    },
    {
        "id": "youtube_subscribe",
        "name": "YouTube 订阅动画",
        "category": "overlay",
        "emotion_tags": ["fun"],
        "description": "YouTube 风格订阅引导,含订阅按钮和计数",
        "variables": [
            {"name": "channel_name", "type": "string", "default": "My Channel", "description": "频道名称", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 36, "description": "字号 px", "min": 20, "max": 80},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "button_color", "type": "color", "default": "#FF0000", "description": "订阅按钮颜色"},
            {"name": "subscriber_count", "type": "string", "default": "10K", "description": "订阅者数显示", "maxLength": 10},
            {"name": "duration", "type": "number", "default": 2.5, "description": "显示时长(秒)", "min": 1, "max": 10},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","back.out(1.7)"], "description": "缓动函数,控制动画速度曲线"},
        ],
    },
    {
        "id": "floating_tag",
        "name": "浮动标签/贴纸",
        "category": "overlay",
        "emotion_tags": ["fun", "warmth"],
        "description": "浮动标签或贴纸效果,轻轻漂浮吸引注意",
        "variables": [
            {"name": "text", "type": "string", "default": "NEW!", "description": "标签文字", "maxLength": 20},
            {"name": "font_size", "type": "number", "default": 32, "description": "字号 px", "min": 16, "max": 80},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "bg_color", "type": "color", "default": "#FF6B6B", "description": "标签背景颜色"},
            {"name": "position", "type": "enum", "default": "top_right", "options": ["top_left","top_right","bottom_left","bottom_right","center"], "description": "标签在屏幕上的位置"},
            {"name": "float_amplitude", "type": "number", "default": 10, "description": "浮动幅度(px)", "min": 2, "max": 50},
            {"name": "float_speed", "type": "number", "default": 2, "description": "浮动速度(秒为一个周期)", "min": 0.5, "max": 10},
            {"name": "duration", "type": "number", "default": 3.0, "description": "显示时长(秒)", "min": 1, "max": 30},
        ],
    },

    # -- scene(场景生成)--

    {
        "id": "product_showcase",
        "name": "产品突出展示",
        "category": "scene",
        "emotion_tags": ["emphasis", "premium"],
        "description": "产品突出展示效果,含 3D 旋转和光环特效",
        "variables": [
            {"name": "product_name", "type": "string", "default": "产品名称", "description": "产品名称", "maxLength": 50},
            {"name": "tagline", "type": "string", "default": "宣传标语", "description": "产品标语", "maxLength": 100},
            {"name": "product_color", "type": "color", "default": "#4A90D9", "description": "产品主题颜色"},
            {"name": "bg_color", "type": "color", "default": "#1A1A2E", "description": "背景颜色"},
            {"name": "font_size", "type": "number", "default": 48, "description": "文字字号 px", "min": 24, "max": 120},
            {"name": "rotation_speed", "type": "number", "default": 3, "description": "旋转周期(秒)", "min": 1, "max": 10},
            {"name": "duration", "type": "number", "default": 5.0, "description": "场景时长(秒)", "min": 2, "max": 30},
            {"name": "ease", "type": "enum", "default": "power2.inOut", "options": ["power1.inOut","power2.inOut","power3.inOut","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "show_shadow", "type": "boolean", "default": True, "description": "是否显示投影"},
            {"name": "shadow_color", "type": "color", "default": "#000000", "description": "投影颜色"},
        ],
    },
    {
        "id": "data_chart",
        "name": "数据图表",
        "category": "scene",
        "emotion_tags": ["emphasis"],
        "description": "数据图表动画,支持柱状图/折线图/饼图/计数",
        "variables": [
            {"name": "chart_type", "type": "enum", "default": "bar", "options": ["bar","line","pie","counter"], "description": "图表类型(bar/line/pie/counter)"},
            {"name": "title", "type": "string", "default": "数据标题", "description": "图表标题", "maxLength": 80},
            {"name": "data_points", "type": "string", "default": '[{"label":"A","value":30},{"label":"B","value":50},{"label":"C","value":20}]', "description": "数据点 JSON 数组", "maxLength": 1000},
            {"name": "bar_color", "type": "color", "default": "#4A90D9", "description": "柱状图/线条颜色"},
            {"name": "font_size", "type": "number", "default": 24, "description": "标签字号 px", "min": 12, "max": 60},
            {"name": "duration", "type": "number", "default": 3.0, "description": "动画时长(秒)", "min": 1, "max": 10},
            {"name": "ease", "type": "enum", "default": "power3.out", "options": ["power1.out","power2.out","power3.out","expo.out","back.out(1.7)"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "show_grid", "type": "boolean", "default": True, "description": "是否显示网格线"},
            {"name": "animation", "type": "enum", "default": "grow", "options": ["grow","fade_in","slide_up","bounce_in"], "description": "图表入场动画类型"},
        ],
    },
    {
        "id": "number_counter",
        "name": "数字滚动计数器",
        "category": "scene",
        "emotion_tags": ["emphasis", "surprise"],
        "description": "数字滚动计数动画,适合统计/指标展示",
        "variables": [
            {"name": "target_number", "type": "number", "default": 10000, "description": "目标数字", "min": 0, "max": 999999999},
            {"name": "prefix", "type": "string", "default": "$", "description": "数字前缀", "maxLength": 10},
            {"name": "suffix", "type": "string", "default": "+", "description": "数字后缀", "maxLength": 10},
            {"name": "font_size", "type": "number", "default": 96, "description": "字号 px", "min": 36, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "数字颜色"},
            {"name": "duration", "type": "number", "default": 2.0, "description": "计数动画时长(秒)", "min": 0.5, "max": 10},
            {"name": "ease", "type": "enum", "default": "power3.out", "options": ["power1.out","power2.out","power3.out","expo.out","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "decimal_places", "type": "number", "default": 0, "description": "小数位数", "min": 0, "max": 4},
            {"name": "separator", "type": "enum", "default": "comma", "options": ["comma","dot","space","none"], "description": "千位分隔符样式"},
        ],
    },
    {
        "id": "split_screen_compare",
        "name": "分屏对比",
        "category": "scene",
        "emotion_tags": ["conflict", "emphasis"],
        "description": "左右分屏对比展示,适合前后对比/AB比较",
        "variables": [
            {"name": "left_label", "type": "string", "default": "Before", "description": "左侧标签", "maxLength": 30},
            {"name": "right_label", "type": "string", "default": "After", "description": "右侧标签", "maxLength": 30},
            {"name": "left_content", "type": "string", "default": "左侧内容", "description": "左侧描述", "maxLength": 200},
            {"name": "right_content", "type": "string", "default": "右侧内容", "description": "右侧描述", "maxLength": 200},
            {"name": "font_size", "type": "number", "default": 32, "description": "标签字号 px", "min": 16, "max": 80},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "divider_color", "type": "color", "default": "#FFFFFF", "description": "分割线颜色"},
            {"name": "duration", "type": "number", "default": 4.0, "description": "场景时长(秒)", "min": 2, "max": 30},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","expo.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "divider_width", "type": "number", "default": 3, "description": "分割线宽度(px)", "min": 1, "max": 10},
            {"name": "split_ratio", "type": "number", "default": 50, "description": "左右比例(百分比)", "min": 20, "max": 80},
        ],
    },
    {
        "id": "brand_intro",
        "name": "品牌开场/Logo 展示",
        "category": "scene",
        "emotion_tags": ["premium"],
        "description": "品牌开场动画,Logo 展示配合标语,适合片头",
        "variables": [
            {"name": "brand_name", "type": "string", "default": "Brand", "description": "品牌名称", "maxLength": 50},
            {"name": "tagline", "type": "string", "default": "Just Do It", "description": "品牌标语", "maxLength": 100},
            {"name": "logo_url", "type": "string", "default": "", "description": "Logo 图片 URL(可留空纯文字)", "maxLength": 500},
            {"name": "font_size", "type": "number", "default": 72, "description": "品牌名字号 px", "min": 36, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "品牌名颜色"},
            {"name": "accent_color", "type": "color", "default": "#FFD700", "description": "强调色"},
            {"name": "duration", "type": "number", "default": 4.0, "description": "场景时长(秒)", "min": 2, "max": 30},
            {"name": "ease", "type": "enum", "default": "power3.out", "options": ["power1.out","power2.out","power3.out","power4.out","expo.out","back.out(1.7)"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "animation_type", "type": "enum", "default": "reveal", "options": ["reveal","zoom_in","glow_up","slide_parts","fade_layers"], "description": "品牌出现动画类型"},
            {"name": "bg_color", "type": "color", "default": "#0A0A0A", "description": "背景颜色"},
        ],
    },
    {
        "id": "quote_card",
        "name": "引用文字卡片",
        "category": "scene",
        "emotion_tags": ["quiet", "narrative"],
        "description": "引用文字卡片,适合名人名言/精彩对白展示",
        "variables": [
            {"name": "quote_text", "type": "string", "default": "生活就像一盒巧克力", "description": "引用文字", "maxLength": 300},
            {"name": "author", "type": "string", "default": "Forrest Gump", "description": "作者/出处", "maxLength": 50},
            {"name": "font_size", "type": "number", "default": 40, "description": "引用文字字号 px", "min": 20, "max": 100},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "accent_color", "type": "color", "default": "#FFD700", "description": "强调色(引号/装饰)"},
            {"name": "bg_color", "type": "color", "default": "#1A1A2E", "description": "背景颜色"},
            {"name": "duration", "type": "number", "default": 4.0, "description": "场景时长(秒)", "min": 2, "max": 30},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","expo.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "border_style", "type": "enum", "default": "left_bar", "options": ["left_bar","frame","rounded","minimal","double_line"], "description": "卡片边框装饰风格"},
            {"name": "quote_icon", "type": "boolean", "default": True, "description": "是否显示引号图标"},
        ],
    },
    {
        "id": "call_to_action",
        "name": "CTA 行动号召",
        "category": "scene",
        "emotion_tags": ["emphasis", "fun"],
        "description": "行动号召按钮,带入场动画,适合引导点击/购买",
        "variables": [
            {"name": "button_text", "type": "string", "default": "立即购买", "description": "按钮文字", "maxLength": 30},
            {"name": "url", "type": "string", "default": "", "description": "链接 URL(可选)", "maxLength": 500},
            {"name": "font_size", "type": "number", "default": 40, "description": "按钮文字字号 px", "min": 20, "max": 100},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "按钮文字颜色"},
            {"name": "bg_color", "type": "color", "default": "#1A1A2E", "description": "背景颜色"},
            {"name": "button_color", "type": "color", "default": "#FF6B6B", "description": "按钮底色"},
            {"name": "duration", "type": "number", "default": 3.0, "description": "场景时长(秒)", "min": 1, "max": 20},
            {"name": "ease", "type": "enum", "default": "power3.out", "options": ["power1.out","power2.out","power3.out","expo.out","back.out(1.7)","elastic.out(1,0.3)"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "animation", "type": "enum", "default": "pulse", "options": ["pulse","slide_up","scale_in","glow","bounce"], "description": "按钮入场动画类型"},
            {"name": "icon", "type": "string", "default": "", "description": "按钮图标(Emoji 或 SVG 路径名)", "maxLength": 50},
        ],
    },

    # -- vfx(视觉特效)--

    {
        "id": "film_grain",
        "name": "胶片颗粒",
        "category": "vfx",
        "emotion_tags": ["cinematic"],
        "description": "模拟胶片颗粒噪点,增加电影感和复古质感",
        "variables": [
            {"name": "intensity", "type": "number", "default": 0.15, "description": "颗粒强度", "min": 0.02, "max": 0.5},
            {"name": "speed", "type": "number", "default": 1, "description": "颗粒变化速度", "min": 0.1, "max": 5},
            {"name": "opacity", "type": "number", "default": 0.3, "description": "颗粒层透明度", "min": 0, "max": 1},
            {"name": "blend_mode", "type": "enum", "default": "overlay", "options": ["overlay","multiply","screen","normal","soft-light"], "description": "图层混合模式"},
            {"name": "grain_size", "type": "number", "default": 1.5, "description": "颗粒大小", "min": 0.5, "max": 5},
        ],
    },
    {
        "id": "vignette",
        "name": "暗角",
        "category": "vfx",
        "emotion_tags": ["cinematic", "premium"],
        "description": "画面边缘暗角效果,聚焦中心视线",
        "variables": [
            {"name": "opacity", "type": "number", "default": 0.5, "description": "暗角强度", "min": 0, "max": 1},
            {"name": "color", "type": "color", "default": "#000000", "description": "暗角颜色"},
            {"name": "size", "type": "number", "default": 0.7, "description": "暗角大小(0-1,越小暗角越大)", "min": 0.1, "max": 1},
            {"name": "feather", "type": "number", "default": 0.5, "description": "边缘羽化程度", "min": 0, "max": 1},
            {"name": "shape", "type": "enum", "default": "circle", "options": ["circle","ellipse","rounded_rect"], "description": "暗角形状"},
        ],
    },
    {
        "id": "glow_bloom",
        "name": "辉光泛光",
        "category": "vfx",
        "emotion_tags": ["dreamy", "premium"],
        "description": "高亮区域辉光泛光特效,梦幻唯美",
        "variables": [
            {"name": "intensity", "type": "number", "default": 0.5, "description": "辉光强度", "min": 0.1, "max": 2},
            {"name": "radius", "type": "number", "default": 30, "description": "辉光半径(px)", "min": 5, "max": 100},
            {"name": "threshold", "type": "number", "default": 0.8, "description": "亮度阈值(高于此值才产生辉光)", "min": 0, "max": 1},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "辉光颜色色调"},
            {"name": "blend_mode", "type": "enum", "default": "screen", "options": ["screen","add","overlay","normal"], "description": "图层混合模式"},
        ],
    },
    {
        "id": "chromatic_aberration",
        "name": "色差故障",
        "category": "vfx",
        "emotion_tags": ["conflict", "tech"],
        "description": "RGB 通道分离色差效果,数字故障风格",
        "variables": [
            {"name": "intensity", "type": "number", "default": 3, "description": "色差偏移强度(px)", "min": 1, "max": 20},
            {"name": "offset_x", "type": "number", "default": 5, "description": "水平偏移(px)", "min": -20, "max": 20},
            {"name": "offset_y", "type": "number", "default": 0, "description": "垂直偏移(px)", "min": -20, "max": 20},
            {"name": "speed", "type": "number", "default": 0, "description": "自动变化速度(0=静态)", "min": 0, "max": 5},
            {"name": "blend_mode", "type": "enum", "default": "screen", "options": ["screen","normal","add","difference"], "description": "图层混合模式"},
        ],
    },
    {
        "id": "particle_overlay",
        "name": "粒子浮动",
        "category": "vfx",
        "emotion_tags": ["dreamy", "fun"],
        "description": "浮动粒子特效,星星/光点/雪花等粒子漂浮运动",
        "variables": [
            {"name": "particle_count", "type": "number", "default": 50, "description": "粒子数量", "min": 5, "max": 500},
            {"name": "particle_size", "type": "number", "default": 3, "description": "粒子大小(px)", "min": 1, "max": 20},
            {"name": "particle_color", "type": "color", "default": "#FFFFFF", "description": "粒子颜色"},
            {"name": "speed", "type": "number", "default": 1, "description": "粒子运动速度", "min": 0.1, "max": 5},
            {"name": "spread", "type": "number", "default": 100, "description": "分布范围(px)", "min": 20, "max": 500},
            {"name": "opacity", "type": "number", "default": 0.6, "description": "粒子透明度", "min": 0, "max": 1},
            {"name": "particle_type", "type": "enum", "default": "circle", "options": ["circle","star","diamond","sparkle","square"], "description": "粒子形状类型"},
        ],
    },

    # -- caption(字幕/弹幕)--

    {
        "id": "karaoke_caption",
        "name": "逐字高亮歌词/字幕",
        "category": "caption",
        "emotion_tags": ["fun"],
        "description": "逐字高亮的歌词或字幕效果,像卡拉 OK 一样",
        "variables": [
            {"name": "text", "type": "string", "default": "歌词内容在这里", "description": "歌词/字幕文字", "maxLength": 200},
            {"name": "font_size", "type": "number", "default": 48, "description": "字号 px", "min": 24, "max": 120},
            {"name": "color", "type": "color", "default": "#888888", "description": "未高亮文字颜色"},
            {"name": "highlight_color", "type": "color", "default": "#FFD700", "description": "高亮文字颜色"},
            {"name": "duration", "type": "number", "default": 3.0, "description": "动画总时长(秒)", "min": 1, "max": 30},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","linear"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
            {"name": "word_timing", "type": "string", "default": "", "description": "逐字时间戳 JSON(如[0,0.5,1.0]),留空均匀分配", "maxLength": 500},
        ],
    },
    {
        "id": "kinetic_type",
        "name": "动态排版",
        "category": "caption",
        "emotion_tags": ["emphasis", "fun"],
        "description": "文字逐字或逐词运动,动态排版吸引眼球",
        "variables": [
            {"name": "text", "type": "string", "default": "动态排版效果", "description": "文字内容", "maxLength": 100},
            {"name": "font_size", "type": "number", "default": 56, "description": "字号 px", "min": 24, "max": 200},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "duration", "type": "number", "default": 2.0, "description": "动画总时长(秒)", "min": 0.5, "max": 10},
            {"name": "ease", "type": "enum", "default": "power3.out", "options": ["power1.out","power2.out","power3.out","power4.out","back.out(1.7)","expo.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
            {"name": "animation_type", "type": "enum", "default": "word_by_word", "options": ["word_by_word","char_by_char","line_by_line","random","stagger"], "description": "文字出现动画类型"},
            {"name": "word_spacing", "type": "number", "default": 10, "description": "字间距(px)", "min": 0, "max": 100},
            {"name": "line_height", "type": "number", "default": 1.5, "description": "行高倍数", "min": 1, "max": 3},
        ],
    },
    {
        "id": "bullet_points",
        "name": "分点列举动画",
        "category": "caption",
        "emotion_tags": ["narrative", "emphasis"],
        "description": "分点逐条显示,配合图标和动画,适合列清单/步骤",
        "variables": [
            {"name": "points", "type": "string", "default": '["要点一","要点二","要点三"]', "description": "要点列表 JSON 数组", "maxLength": 1000},
            {"name": "font_size", "type": "number", "default": 36, "description": "字号 px", "min": 16, "max": 80},
            {"name": "color", "type": "color", "default": "#FFFFFF", "description": "文字颜色"},
            {"name": "accent_color", "type": "color", "default": "#4A90D9", "description": "项目符号颜色"},
            {"name": "duration", "type": "number", "default": 4.0, "description": "动画总时长(秒)", "min": 1, "max": 20},
            {"name": "ease", "type": "enum", "default": "power2.out", "options": ["power1.out","power2.out","power3.out","back.out(1.7)","expo.out"], "description": "缓动函数,控制动画速度曲线"},
            {"name": "delay", "type": "number", "default": 0, "description": "延迟开始(秒)", "min": 0, "max": 5},
            {"name": "spacing", "type": "number", "default": 20, "description": "点间距(px)", "min": 5, "max": 80},
            {"name": "bullet_style", "type": "enum", "default": "dot", "options": ["dot","number","checkmark","arrow","star","dash"], "description": "项目符号样式"},
            {"name": "animation", "type": "enum", "default": "slide_left", "options": ["slide_left","fade_in","scale_in","slide_up"], "description": "逐条出现动画类型"},
        ],
    },
]


# ===== 辅助函数 =====

_CATEGORY_NAMES = {
    "entrance": "入场动效",
    "emphasis": "强调动效",
    "exit": "退场动效",
    "transition": "转场",
    "overlay": "叠层",
    "scene": "场景生成",
    "vfx": "视觉特效",
    "caption": "字幕/弹幕",
}

_CSS_COLORS = {
    "aliceblue","antiquewhite","aqua","aquamarine","azure","beige","bisque",
    "black","blanchedalmond","blue","blueviolet","brown","burlywood","cadetblue",
    "chartreuse","chocolate","coral","cornflowerblue","cornsilk","crimson","cyan",
    "darkblue","darkcyan","darkgoldenrod","darkgray","darkgreen","darkgrey",
    "darkkhaki","darkmagenta","darkolivegreen","darkorange","darkorchid","darkred",
    "darksalmon","darkseagreen","darkslateblue","darkslategray","darkslategrey",
    "darkturquoise","darkviolet","deeppink","deepskyblue","dimgray","dimgrey",
    "dodgerblue","firebrick","floralwhite","forestgreen","fuchsia","gainsboro",
    "ghostwhite","gold","goldenrod","gray","green","greenyellow","grey","honeydew",
    "hotpink","indianred","indigo","ivory","khaki","lavender","lavenderblush",
    "lawngreen","lemonchiffon","lightblue","lightcoral","lightcyan",
    "lightgoldenrodyellow","lightgray","lightgreen","lightgrey","lightpink",
    "lightsalmon","lightseagreen","lightskyblue","lightslategray","lightslategrey",
    "lightsteelblue","lightyellow","lime","limegreen","linen","magenta","maroon",
    "mediumaquamarine","mediumblue","mediumorchid","mediumpurple","mediumseagreen",
    "mediumslateblue","mediumspringgreen","mediumturquoise","mediumvioletred",
    "midnightblue","mintcream","mistyrose","moccasin","navajowhite","navy","oldlace",
    "olive","olivedrab","orange","orangered","orchid","palegoldenrod","palegreen",
    "paleturquoise","palevioletred","papayawhip","peachpuff","peru","pink","plum",
    "powderblue","purple","rebeccapurple","red","rosybrown","royalblue","saddlebrown",
    "salmon","sandybrown","seagreen","seashell","sienna","silver","skyblue",
    "slateblue","slategray","slategrey","snow","springgreen","steelblue","tan","teal",
    "thistle","tomato","turquoise","violet","wheat","white","whitesmoke","yellow",
    "yellowgreen",
}


def _validate_type(value, schema: dict) -> tuple:
    """
    校验单个变量值是否符合 schema 中声明的类型.

    返回值: (is_valid, error_message)
    """
    var_type = schema["type"]
    var_name = schema["name"]

    if var_type == "string":
        if not isinstance(value, str):
            return False, "%s: 应为字符串,收到 %s" % (var_name, type(value).__name__)
        max_len = schema.get("maxLength")
        if max_len is not None and len(value) > max_len:
            return False, "%s: 超出最大长度 %d(当前 %d)" % (var_name, max_len, len(value))
        return True, ""

    if var_type == "number":
        if not isinstance(value, (int, float)):
            return False, "%s: 应为数字,收到 %s" % (var_name, type(value).__name__)
        vmin = schema.get("min")
        vmax = schema.get("max")
        if vmin is not None and value < vmin:
            return False, "%s: 不能小于 %s" % (var_name, str(vmin))
        if vmax is not None and value > vmax:
            return False, "%s: 不能大于 %s" % (var_name, str(vmax))
        return True, ""

    if var_type == "color":
        if not isinstance(value, str):
            return False, "%s: 颜色应为字符串,收到 %s" % (var_name, type(value).__name__)
        if not re.match(r"^#[0-9a-fA-F]{3,8}$", value) and value.lower() not in (
            "transparent", "inherit", "currentcolor"
        ) and value.lower() not in _CSS_COLORS:
            return False, "%s: 无效的颜色值 '%s'" % (var_name, value)
        return True, ""

    if var_type == "boolean":
        if not isinstance(value, bool):
            return False, "%s: 应为布尔值,收到 %s" % (var_name, type(value).__name__)
        return True, ""

    if var_type == "enum":
        if not isinstance(value, str):
            return False, "%s: 枚举值应为字符串,收到 %s" % (var_name, type(value).__name__)
        options = schema.get("options", [])
        if value not in options:
            opts_str = ", ".join(options)
            return False, "%s: '%s' 不在可选值中 [%s]" % (var_name, value, opts_str)
        return True, ""

    return False, "%s: 未知类型 '%s'" % (var_name, var_type)


def _validate_variables(template: dict, variables: dict) -> tuple:
    """
    对模板的所有变量做类型校验.

    返回值: (all_valid, error_message)
    """
    schema_map = {}
    for var_schema in template.get("variables", []):
        schema_map[var_schema["name"]] = var_schema

    for var_name, var_value in variables.items():
        if var_name not in schema_map:
            return False, "未知变量 '%s',模板 '%s' 无此参数" % (var_name, template["id"])
        schema = schema_map[var_name]
        valid, err = _validate_type(var_value, schema)
        if not valid:
            return False, err

    return True, ""


def _find_template(template_id: str) -> Optional[dict]:
    """根据 ID 查找模板"""
    for t in _TEMPLATE_CATALOG:
        if t["id"] == template_id:
            return t
    return None


# ===== HTML 生成 =====

def _generate_slide_up_html(variables: dict) -> str:
    """生成 slide_up_text 的 composition HTML"""
    text = variables.get("text", "Hello")
    font_size = variables.get("font_size", 64)
    color = variables.get("color", "#FFFFFF")
    duration = variables.get("duration", 0.8)
    ease = variables.get("ease", "power3.out")
    delay = variables.get("delay", 0)
    total_dur = duration + delay + 0.5

    html = '<!DOCTYPE html>\n'
    html += '<html>\n<head>\n'
    html += '<style>\n'
    html += '  body { margin:0; width:1280px; height:720px; overflow:hidden; background:transparent; display:flex; align-items:center; justify-content:center; }\n'
    html += '  #main { font-size:%dpx; color:%s; font-family:sans-serif; opacity:0; transform:translateY(50px); }\n' % (font_size, color)
    html += '</style>\n'
    html += '<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>\n'
    html += '</head>\n<body>\n'
    html += '<div id="main" data-composition-id="main" data-width="1280" data-height="720" data-duration="%.1f">\n' % total_dur
    html += '  <span class="clip" data-start="%.1f" data-duration="%.1f" data-track-index="0">%s</span>\n' % (delay, duration, text)
    html += '</div>\n'
    html += '<script>\n'
    html += '(function(){\n'
    html += '  var tl = gsap.timeline({paused:true});\n'
    html += '  tl.to("#main", {duration:%.1f, y:0, opacity:1, ease:"%s"}, "%.1f");\n' % (duration, ease, delay)
    html += '  window.__timelines = window.__timelines || {};\n'
    html += '  window.__timelines["main"] = tl;\n'
    html += '  window.__hf = window.__hf || {};\n'
    html += '  window.__hf.duration = %.1f;\n' % total_dur
    html += '  window.__hf.seek = function(t){ tl.seek(t); };\n'
    html += '})();\n'
    html += '</script>\n'
    html += '</body>\n</html>'
    return html


def _generate_composition_html(template_id: str, variables: dict) -> str:
    """
    根据模板 ID 和变量生成对应的 composition HTML.

    注意:实际 HF composition 由 Electron 浏览器加载渲染,
    这里只负责生成正确的 HTML 骨架 + GSAP 时间线.

    规范要求:
    - 根元素有 data-composition-id, data-width, data-height, data-duration
    - 时间线元素有 data-start, data-duration, data-track-index, class="clip"
    - GSAP timeline 有 {paused: true},注册到 window.__timelines
    """
    if template_id == "slide_up_text":
        return _generate_slide_up_html(variables)

    template = _find_template(template_id)
    if not template:
        raise ValueError("模板不存在: " + template_id)

    text = variables.get("text", "")
    font_size = variables.get("font_size", 64)
    color = variables.get("color", "#FFFFFF")
    duration = variables.get("duration", 1.0)
    delay = variables.get("delay", 0)
    hold = variables.get("hold_duration", 0.5)
    total_dur = duration + delay + hold

    content = text or variables.get("title", "") or variables.get("button_text", "")
    if not content:
        content = variables.get("brand_name", "") or variables.get("quote_text", "")
    if not content:
        content = template_id

    extra_styles = ""
    bg_color = variables.get("bg_color", "")
    if bg_color:
        extra_styles += "background:" + bg_color + ";"

    html = '<!DOCTYPE html>\n'
    html += '<html>\n<head>\n'
    html += '<style>\n'
    html += '  body { margin:0; width:1280px; height:720px; overflow:hidden; background:transparent; display:flex; align-items:center; justify-content:center; %s }\n' % extra_styles
    html += '  #main { font-size:%dpx; color:%s; font-family:sans-serif; }\n' % (font_size, color)
    html += '</style>\n'
    html += '<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>\n'
    html += '</head>\n<body>\n'
    html += '<div id="main" data-composition-id="%s" data-width="1280" data-height="720" data-duration="%.1f">\n' % (template_id, total_dur)
    html += '  <span class="clip" data-start="%.1f" data-duration="%.1f" data-track-index="0">%s</span>\n' % (delay, duration, content)
    html += '</div>\n'
    html += '<script>\n'
    html += '(function(){\n'
    html += '  var tl = gsap.timeline({paused:true});\n'
    html += '  tl.fromTo("#main", {opacity:0}, {duration:%.1f, opacity:1, ease:"power2.out"}, "%.1f");\n' % (duration, delay)
    html += '  window.__timelines = window.__timelines || {};\n'
    html += '  window.__timelines["main"] = tl;\n'
    html += '  window.__hf = window.__hf || {};\n'
    html += '  window.__hf.duration = %.1f;\n' % total_dur
    html += '  window.__hf.seek = function(t){ tl.seek(t); };\n'
    html += '})();\n'
    html += '</script>\n'
    html += '</body>\n</html>'
    return html


# ===== 工具函数 =====

@tool(
    name="list_hf_templates",
    description="按分类,情绪或关键词列出 HyperFrames 模板目录.在策划阶段调用此工具浏览可用模板",
    phase="plan",
    category="effect",
    tags=["hf", "template", "catalog", "list"],
    group="花字与动画(效果层)",
)
def list_hf_templates(category: str = "", emotion: str = "", keyword: str = "") -> str:
    """
    列出可用的 HyperFrames 模板,可按分类/情绪/关键词过滤.

    Args:
        category: 分类筛选(entrance/emphasis/exit/transition/overlay/scene/vfx/caption),留空列出所有
        emotion: 情绪标签筛选(如 smooth/emphasis/fun/premium 等),留空不筛选
        keyword: 关键词搜索模板名称或描述,留空不筛选

    Returns:
        格式化的模板目录文本
    """
    results = list(_TEMPLATE_CATALOG)

    if category:
        results = [t for t in results if t["category"] == category]

    if emotion:
        emo_lower = emotion.lower()
        results = [
            t for t in results
            if any(emo_lower in tag.lower() for tag in t.get("emotion_tags", []))
        ]

    if keyword:
        kw_lower = keyword.lower()
        results = [
            t for t in results
            if kw_lower in t["name"].lower()
            or kw_lower in t["description"].lower()
            or kw_lower in t["id"].lower()
        ]

    if not results:
        return "(无匹配模板)"

    grouped = {}
    for t in results:
        cat = t["category"]
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(t)

    lines = []
    if category:
        cat_display = _CATEGORY_NAMES.get(category, category)
        lines.append("### %s (%s) — 共 %d 个模板" % (cat_display, category, len(results)))
        lines.append("")
        for i, t in enumerate(results, 1):
            tags_str = ", ".join(t.get("emotion_tags", []))
            lines.append("%d. %s — %s [%s]" % (i, t["id"], t["name"], tags_str))
            lines.append("   " + t["description"])
        lines.append("")
    else:
        cat_order = ["entrance","emphasis","exit","transition","overlay","scene","vfx","caption"]
        for cat_name in cat_order:
            if cat_name not in grouped:
                continue
            cat_display = _CATEGORY_NAMES.get(cat_name, cat_name)
            templates = grouped[cat_name]
            lines.append("### %s (%s) — 共 %d 个模板" % (cat_display, cat_name, len(templates)))
            lines.append("")
            for i, t in enumerate(templates, 1):
                tags_str = ", ".join(t.get("emotion_tags", []))
                lines.append("%d. %s — %s [%s]" % (i, t["id"], t["name"], tags_str))
                lines.append("   " + t["description"])
            lines.append("")

    return "\n".join(lines)


@tool(
    name="get_hf_template_schema",
    description="获取单个 HyperFrames 模板的完整参数定义,包括所有变量的类型/默认值/可选值/说明",
    phase="plan",
    category="effect",
    tags=["hf", "template", "schema"],
    group="花字与动画(效果层)",
)
def get_hf_template_schema(template_id: str) -> str:
    """
    获取单个 HF 模板的完整 schema,包括所有变量定义.

    Args:
        template_id: 模板 ID(如 "slide_up_text"),从 list_hf_templates 获取

    Returns:
        JSON 格式的模板完整信息
    """
    template = _find_template(template_id)
    if not template:
        available = [t["id"] for t in _TEMPLATE_CATALOG]
        return json.dumps(
            {"error": "模板不存在: " + template_id, "available_ids": available},
            ensure_ascii=False,
            indent=2,
        )

    result = {
        "id": template["id"],
        "name": template["name"],
        "category": template["category"],
        "category_label": _CATEGORY_NAMES.get(template["category"], template["category"]),
        "emotion_tags": template["emotion_tags"],
        "description": template["description"],
        "variables": template["variables"],
        "variable_count": len(template["variables"]),
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@tool(
    name="generate_hf_composition",
    description="选择 HF 模板 + 填入参数 -> 生成完整的 composition HTML 文件.phase=edit.返回生成的 HTML 文件路径和内容摘要",
    phase="edit",
    category="effect",
    tags=["hf", "template", "composition", "generate"],
    group="花字与动画(效果层)",
)
def generate_hf_composition(
    template_id: str,
    variables: dict,
    output_path: str = "",
    width: int = 1280,
    height: int = 720,
) -> str:
    """
    选择模板并填入参数,生成完整的 HF composition HTML 文件.

    Args:
        template_id: 模板 ID(如 "slide_up_text")
        variables: 参数字典,key=变量名, value=值.所有值会做类型校验
        output_path: 输出 HTML 文件路径.留空则自动生成到 drafts 目录
        width: composition 宽度(默认 1280)
        height: composition 高度(默认 720)

    Returns:
        JSON 字符串:生成的 HTML 文件路径 + 内容摘要
    """
    template = _find_template(template_id)
    if not template:
        available = [t["id"] for t in _TEMPLATE_CATALOG]
        return json.dumps(
            {"error": "模板不存在: " + template_id, "available_ids": available},
            ensure_ascii=False,
        )

    valid, err_msg = _validate_variables(template, variables)
    if not valid:
        return json.dumps({"error": "参数校验失败: " + err_msg}, ensure_ascii=False)

    merged = {}
    for var_schema in template["variables"]:
        var_name = var_schema["name"]
        if var_name in variables:
            merged[var_name] = variables[var_name]
        else:
            merged[var_name] = var_schema["default"]

    try:
        html = _generate_composition_html(template_id, merged)
    except Exception as e:
        return json.dumps({"error": "HTML 生成失败: " + str(e)}, ensure_ascii=False)

    if not output_path:
        _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_id = template_id.replace("/", "_").replace("\\", "_")
        hash_suffix = hashlib.md5(json.dumps(variables, sort_keys=True).encode()).hexdigest()[:6]
        output_path = str(_DRAFTS_DIR / ("%s_%s_%s.html" % (safe_id, ts, hash_suffix)))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    file_size = os.path.getsize(output_path)

    summary = html.strip()[:200].replace("\n", " ").replace("\r", "")
    result = {
        "success": True,
        "template_id": template_id,
        "template_name": template["name"],
        "output_path": os.path.abspath(output_path),
        "file_size_bytes": file_size,
        "content_summary": summary + ("..." if len(html) > 200 else ""),
        "variables_used": {
            k: str(v) if isinstance(v, (dict, list)) else v
            for k, v in merged.items()
        },
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool(
    name="preview_hf_template",
    description="生成 HF composition HTML 并用 npx hyperframes snapshot 截图第一帧.phase=plan.返回截图路径或 HTML 路径",
    phase="plan",
    category="effect",
    tags=["hf", "template", "preview", "snapshot"],
    group="花字与动画(效果层)",
)
def preview_hf_template(template_id: str, variables: Optional[dict] = None) -> str:
    """
    对模板生成 composition HTML 并截图第一帧.

    先调用 generate_hf_composition 生成 HTML,
    再使用 npx hyperframes snapshot 截图.

    Args:
        template_id: 模板 ID
        variables: 变量参数字典(可选).留空使用模板默认参数

    Returns:
        截图文件路径(或 HTML 路径,截图失败时)
    """
    if variables is None:
        variables = {}

    gen_result = generate_hf_composition(template_id=template_id, variables=variables)
    try:
        gen_data = json.loads(gen_result) if isinstance(gen_result, str) else gen_result or {}
    except (json.JSONDecodeError, TypeError):
        gen_data = {}

    if "error" in gen_data:
        return json.dumps({"error": gen_data["error"]}, ensure_ascii=False)

    html_path = gen_data["output_path"]

    snapshot_dir = _DATA_ROOT / "previews"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = template_id.replace("/", "_").replace("\\", "_")
    snapshot_path = str(snapshot_dir / ("%s_%s.png" % (safe_id, ts)))

    try:
        cmd = [
            "npx", "hyperframes", "snapshot",
            "--input", html_path,
            "--output", snapshot_path,
            "--time", "0",
            "--width", str(gen_data.get("width", 1280)),
            "--height", str(gen_data.get("height", 720)),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60000,
            cwd=str(_PROJECT_DIR),
            encoding="utf-8",
        )

        if result.returncode == 0 and os.path.exists(snapshot_path):
            response = {
                "success": True,
                "template_id": template_id,
                "html_path": html_path,
                "snapshot_path": os.path.abspath(snapshot_path),
                "method": "hyperframes_snapshot",
            }
        else:
            raise RuntimeError(result.stderr or result.stdout or "snapshot failed")
    except Exception as e:
        response = {
            "success": True,
            "template_id": template_id,
            "html_path": html_path,
            "snapshot_path": None,
            "warning": "截图失败(%s),HTML 文件已生成可手动预览" % str(e),
        }

    return json.dumps(response, ensure_ascii=False, indent=2)


@tool(
    name="render_hf_to_draft",
    description=(
        "将 HF composition HTML 渲染为视频并插入到 Draft 时间线."
        "接受可选的 font_path/font_mapping 在渲染前嵌入字体."
        "先 generate_hf_composition() 生成 HTML,再调用此工具渲染+回填."
        "phase=edit.返回视频路径和 segment ID"
    ),
    phase="edit",
    category="effect",
    tags=["hf", "render", "draft", "timeline", "scene"],
    group="花字与动画(效果层)",
)
def render_hf_to_draft(
    html_path: str,
    draft_id: str = "",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    duration: float = 0,
    description: str = "AI 生成场景",
    format: str = "mov",
    # ── 字体注入(渲染前嵌入)──
    font_path: str = "",
    font_mapping: str = "",
) -> str:
    """
    将 HF composition HTML 渲染为视频,插入到 Draft 时间线末尾.

    Args:
        html_path: composition HTML 文件路径(来自 generate_hf_composition 的 output_path)
        draft_id: 目标草稿 ID.留空从 pipeline_state 读取
        width: 分辨率宽(默认 1920)
        height: 分辨率高(默认 1080)
        fps: 帧率(默认 30)
        duration: 片段时长.<=0 则从 HTML 的 data-duration 推断
        description: 片段描述
        format: 视频格式(mov=带alpha / mp4 / webm)
        font_path: 单字体路径(如 "fonts/SourceHanSerifCN-Heavy.otf"),会替换 {font_family_primary}
        font_mapping: JSON 字符串,多字体映射如 '{"{font_family_primary}": "fonts/xxx.ttf", "{font_family_deco}": "fonts/yyy.ttf"}'

    Returns:
        JSON:渲染结果 + segment 信息
    """
    # 1. 读 HTML 内容
    html_path = html_path.strip()
    if not os.path.exists(html_path):
        return json.dumps({"error": "HTML 文件不存在: " + html_path}, ensure_ascii=False)
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # 从 HTML 推断 duration(如果没传)
    if duration <= 0:
        m = re.search(r'data-duration\s*=\s*["\'](\d+(?:\.\d+)?)["\']', html_content)
        if m:
            duration = float(m.group(1))
        else:
            duration = 0  # 先标记为0,渲染后用 ffprobe 获取实际时长

    # ── 渲染前字体注入 ──
    if font_path or font_mapping:
        from hf_engine.templates.flower_text import embed_font, embed_fonts_from_library
        if font_mapping:
            try:
                mapping = json.loads(font_mapping) if isinstance(font_mapping, str) else font_mapping
                html_content = embed_fonts_from_library(html_content, mapping)
            except (json.JSONDecodeError, TypeError) as e:
                return json.dumps({"error": "font_mapping JSON 解析失败: " + str(e)}, ensure_ascii=False)
        elif font_path:
            html_content = embed_font(html_content, font_path)

    # 2. 渲染为视频
    output_dir = _DATA_ROOT / "generated_scenes"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = "mov" if format == "mov" else ("mp4" if format == "mp4" else "webm")
    output_path = str(output_dir / ("scene_%s.%s" % (ts, ext)))

    try:
        from hf_engine.hf_cli import render_html as hf_render
        actual_output = hf_render(
            html_content,
            output_path,
            width=width,
            height=height,
            fps=fps,
            format=format,
        )
    except Exception as e:
        return json.dumps({
            "error": "HF 渲染失败: " + str(e),
            "html_path": os.path.abspath(html_path),
        }, ensure_ascii=False)

    if not os.path.exists(actual_output):
        return json.dumps({"error": "渲染完成但输出文件不存在: " + str(actual_output)}, ensure_ascii=False)

    # 如果 duration 仍是 fallback 值,用 ffprobe 获取实际视频时长
    if duration <= 0:
        try:
            probe_result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", actual_output],
                capture_output=True, text=True, timeout=30
            )
            if probe_result.returncode == 0:
                info = json.loads(probe_result.stdout)
                duration = float(info["format"]["duration"])
            else:
                duration = 5.0
        except Exception:
            duration = 5.0

    file_size_mb = os.path.getsize(actual_output) / (1024 * 1024)

    # 3. 插入 Draft
    segment_info = None
    draft_error = None
    if draft_id:
        try:
            from director.draft import Draft
            d = Draft(draft_id)
            if d.load():
                seg = d.append_segment(
                    source_path=actual_output,
                    duration=duration,
                    description=description,
                )
                d.save()
                segment_info = {
                    "segment_id": seg["id"],
                    "duration": seg["duration"],
                    "status": seg["status"],
                }
            else:
                draft_error = "草稿 %s 加载失败" % draft_id
        except Exception as e:
            draft_error = "插入 Draft 失败: " + str(e)

    result = {
        "success": True,
        "template_html": os.path.abspath(html_path),
        "video_output": os.path.abspath(actual_output),
        "file_size_mb": round(file_size_mb, 1),
        "duration_seconds": duration,
    }
    if segment_info:
        result["segment"] = segment_info
    if draft_error:
        result["draft_warning"] = draft_error

    return json.dumps(result, ensure_ascii=False, indent=2)
