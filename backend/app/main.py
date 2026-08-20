from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.db import init_database


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    yield


settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)
