from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from .api import router
from .config import PROJECT_ROOT, get_settings
from .db import SessionLocal, create_all
from .models import Teacher
from .services.seed import bootstrap_admin, import_sample_workbook

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    with SessionLocal() as db:
        bootstrap_admin(db, settings.bootstrap_admin_username, settings.bootstrap_admin_password)
        teacher_count = int(db.scalar(select(func.count(Teacher.id))) or 0)
        sample = PROJECT_ROOT / "data" / "imports" / "sample.xlsx"
        if teacher_count == 0 and sample.exists():
            import_sample_workbook(db, sample)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="途排智策前后端分离 MVP API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "health": f"{settings.api_prefix}/health/live",
    }


def run() -> None:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
