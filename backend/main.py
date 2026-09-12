"""《喵星破晓》后端启动入口（FastAPI + SQLAlchemy 2.0 async + MySQL 8）。

本地启动：``cd backend && uvicorn main:app --reload``
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.api_v1 import api_router
from app.core.config import STATIC_DIR, get_settings
from app.core.database import dispose_engine
from app.core.errors import register_exception_handlers

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger = logging.getLogger("dawn_meow")
    logger.info("喵星破晓后端启动：数据库 = %s", settings.sqlalchemy_url.split("@")[-1])
    yield
    await dispose_engine()
    logger.info("喵星破晓后端已关闭")


app = FastAPI(
    title="喵星破晓 Dawn for Meow API",
    version="1.0.0",
    description="单机本地运行的放置类经营 / 星际 4X 挂机游戏后端（Milestone 1）",
    lifespan=lifespan,
)

# 本机前端（Vite 默认端口）跨域放行；纯 Localhost 自娱，无公网部署
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(api_router, prefix="/api/v1")

# 策划定义数据（jobs.json / facilities.json / ...）直接以静态文件提供给前端读取：
# "定义进 JSON"原则的落地方式，前端不重复抄一份定义，也无需为它新增 API 接口。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", tags=["meta"])
async def root() -> dict:
    """非 API 前缀的落地页提示（接口清单仍以 /api/v1 为准）。"""
    return {
        "name": "喵星破晓 Dawn for Meow",
        "version": app.version,
        "api_prefix": "/api/v1",
        "docs": "/docs",
    }
