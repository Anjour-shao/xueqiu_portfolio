"""同步任务协作式取消。"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class SyncCancelled(Exception):
    """用户中止同步。"""


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SyncCancelled("用户已停止同步")
