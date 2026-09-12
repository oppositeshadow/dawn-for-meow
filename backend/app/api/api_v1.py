"""路由聚合器：全部接口统一挂在 `/api/v1` 前缀下（代码结构稿 §4）。"""

from fastapi import APIRouter

from app.api.endpoints import colony, military, radio, tech

api_router = APIRouter()
api_router.include_router(colony.router)
api_router.include_router(radio.router)
api_router.include_router(tech.router)
api_router.include_router(military.router)

__all__ = ["api_router"]
