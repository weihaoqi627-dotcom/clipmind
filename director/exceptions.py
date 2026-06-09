"""
ClipMind 异常层级 — 生产级错误分类.

每条规则:
- 永远不要 except Exception 然后 pass,必须至少记日志
- 抛出具体子类,不要抛 ClipMindError 基类
- 所有异常都有 code 字段,方便前端/日志检索
"""

from typing import Any


class ClipMindError(Exception):
    """所有 ClipMind 异常的基类.禁止直接抛出."""

    def __init__(self, message: str, *, code: str = "", detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.__class__.__name__,
            "message": str(self),
            "code": self.code,
            "detail": self.detail,
        }


# ── 配置 ──

class ConfigError(ClipMindError):
    """配置相关:缺 API key,无效 config.json,模型名错误."""


# ── 输入 ──

class InputError(ClipMindError):
    """输入校验:参数缺失,格式错误,值超出范围."""


# ── 工具 ──

class ToolError(ClipMindError):
    """工具执行失败."""

    def __init__(self, message: str, *, tool_name: str = "", code: str = "", detail: dict[str, Any] | None = None):
        super().__init__(message, code=code, detail=detail)
        self.tool_name = tool_name

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["tool_name"] = self.tool_name
        return d


# ── 渲染 ──

class RenderError(ClipMindError):
    """渲染失败:ffmpeg 崩溃,BMF 异常,输出格式问题."""

    def __init__(self, message: str, *, stage: str = "", code: str = "", detail: dict[str, Any] | None = None):
        super().__init__(message, code=code, detail=detail)
        self.stage = stage

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["stage"] = self.stage
        return d


# ── 管线 ──

class PipelineError(ClipMindError):
    """管线编排错误:阶段失败,状态不一致."""

    def __init__(self, message: str, *, stage: str = "", code: str = "", detail: dict[str, Any] | None = None):
        super().__init__(message, code=code, detail=detail)
        self.stage = stage

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["stage"] = self.stage
        return d


# ── 外部 API ──

class APIError(ClipMindError):
    """外部 API 调用失败:LLM 超时,DashScope 限流,网络问题."""

    def __init__(self, message: str, *, status_code: int = 0, code: str = "", detail: dict[str, Any] | None = None):
        super().__init__(message, code=code, detail=detail)
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["status_code"] = self.status_code
        return d


# ── 存储 ──

class StorageError(ClipMindError):
    """存储错误:JSON 损坏,磁盘满,权限不足."""

    def __init__(self, message: str, *, path: str = "", code: str = "", detail: dict[str, Any] | None = None):
        super().__init__(message, code=code, detail=detail)
        self.path = path

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["path"] = self.path
        return d


# ── 认证 ──

class AuthError(ClipMindError):
    """认证/授权错误."""
