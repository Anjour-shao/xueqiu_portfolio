"""同步任务结构化日志收集（支持实时推送）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class LogSink:
    def __init__(self, emit: Callable[[dict[str, str]], None] | None = None) -> None:
        self._emit = RetryEmit(emit) if emit else None
        self.lines: list[dict[str, str]] = []

    def add(self, level: str, message: str) -> None:
        item = {"level": level, "message": message}
        self.lines.append(item)
        if self._emit is not None:
            self._emit.push(item)

    def info(self, message: str) -> None:
        self.add("info", message)

    def success(self, message: str) -> None:
        self.add("success", message)

    def warn(self, message: str) -> None:
        self.add("warn", message)

    def error(self, message: str) -> None:
        self.add("error", message)


class RetryEmit:
    """包装 emit，避免回调异常中断同步。"""

    def __init__(self, emit: Callable[[dict[str, str]], None]) -> None:
        self._emit = emit

    def push(self, item: dict[str, str]) -> None:
        try:
            self._emit(item)
        except Exception:
            pass


def print_adapter(sink: LogSink) -> Callable[[str], None]:
    """将 sync_quotes 的 print 风格 log 转为 LogSink。"""

    def _log(message: str) -> None:
        level = "info"
        stripped = message.strip()
        if "失败" in stripped or "错误" in stripped:
            level = "error"
        elif "完成" in stripped or "写入" in stripped:
            level = "success"
        sink.add(level, stripped)

    return _log
