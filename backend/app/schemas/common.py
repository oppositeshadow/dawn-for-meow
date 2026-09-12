"""统一响应封装助手（代码结构稿 §4.1）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

CODE_OK = 200


class ApiEnvelope(BaseModel):
    """成功响应：{ "code": 200, "data": { ... } }"""

    code: int = CODE_OK
    data: Any = None


def ok(data: Any = None) -> dict[str, Any]:
    return {"code": CODE_OK, "data": data}
