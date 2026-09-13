"""路由聚合器：全部接口统一挂在 `/api/v1` 前缀下（代码结构稿 §4）。"""

from fastapi import APIRouter

from app.api.endpoints import (
    colony,
    darknet,
    doctrine,
    game,
    garden,
    military,
    minigame,
    planet,
    radio,
    save,
    stats,
    tech,
)

api_router = APIRouter()
api_router.include_router(colony.router)
api_router.include_router(game.router)
api_router.include_router(radio.router)
api_router.include_router(tech.router)
api_router.include_router(military.router)
api_router.include_router(garden.router)
api_router.include_router(darknet.router)
api_router.include_router(planet.router)
api_router.include_router(minigame.router)
api_router.include_router(stats.router)
api_router.include_router(save.router)
api_router.include_router(doctrine.router)

__all__ = ["api_router"]
