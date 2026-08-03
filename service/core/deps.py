"""启动资源（lifespan）+ 请求依赖。

- lifespan：启动时构建统一 KP 注册表（kp_id ↔ idx ↔ name），挂到 app.state。
- get_kp_registry：请求期取注册表（所有 KP 标识转换都走它）。
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.requests import Request

from .kp_registry import KPRegistry, build_registry, N_KP
from .logging import setup_logging

log = logging.getLogger("recommend.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    app.state.kp_registry = build_registry()
    log.info(f"startup ok: kp_registry={len(app.state.kp_registry.id2idx)} ids, N_KP={N_KP}")
    yield
    log.info("shutdown")


def get_kp_registry(request: Request) -> KPRegistry:
    """请求依赖：取启动时建好的统一 KP 注册表。"""
    return request.app.state.kp_registry
