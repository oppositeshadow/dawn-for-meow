"""多槽位存档接口（模块 N4，代码结构稿 §4.10）。

* `GET  /save/slots`       —— 槽位列表与元信息
* `GET  /save/export?slot=1` —— 导出 Base64 存档（Gzip + SHA-256 校验和）
* `POST /save/import`      —— 导入并校验存档（整事务替换）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.models import SaveSlot
from app.schemas.save import SaveEnvelope, SaveExportResponse, SaveImportRequest
from app.services import save_service

router = APIRouter(tags=["save"])


@router.get("/save/slots", response_model=SaveEnvelope)
async def list_slots(session: AsyncSession = Depends(get_session)) -> SaveEnvelope:
    data = await save_service.slots_view(session)
    return SaveEnvelope(code=200, data=data)


@router.get("/save/export", response_model=SaveExportResponse)
async def export_save(
    slot: int = Query(default=1, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
) -> SaveExportResponse:
    data = await save_service.export_slot(session, slot_id=slot)
    return SaveExportResponse(code=200, **data)


@router.post("/save/import", response_model=SaveEnvelope)
async def import_save(
    payload: SaveImportRequest,
    session: AsyncSession = Depends(get_session),
) -> SaveEnvelope:
    data = await save_service.import_slot(session, payload.slot, payload.base64_payload)
    if payload.slot_name:
        save = await session.get(SaveSlot, payload.slot, populate_existing=True)
        if save is not None:
            save.slot_name = payload.slot_name
            await session.commit()
            data["slot_name"] = save.slot_name
    return SaveEnvelope(code=200, data=data)
