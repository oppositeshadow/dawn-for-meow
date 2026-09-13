"""存档接口契约（模块 N4，代码结构稿 §4.10）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SaveImportRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3, description="目标槽位，导入即整体替换")
    base64_payload: str = Field(min_length=1, description="导出得到的 Base64+Gzip 文本")
    slot_name: str | None = Field(default=None, max_length=32, description="可选：顺手改名")


class SaveEnvelope(BaseModel):
    code: int = 200
    data: dict[str, Any]


class SaveExportResponse(BaseModel):
    """按《数据库设计定稿》§8.5：导出直接给出 payload / checksum / save_version。"""

    code: int = 200
    slot: int
    base64_payload: str
    checksum: str
    save_version: int
    raw_bytes: int
    total_rows: int
