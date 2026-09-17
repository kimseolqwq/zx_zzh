from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import BASE_DIR, settings
from app.database import SessionLocal, create_schema
from app.routers import admin, web
from app.seed import initialize_database
from app.services.ollama import OllamaClient


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_schema()
    with SessionLocal() as db:
        initialize_database(db)
    yield


app = FastAPI(
    title="择机 · 多模型手机推荐系统",
    description="本地部署的手机数据、价格与多模型融合推荐系统",
    version="1.4.0",
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="phone_admin_session",
    max_age=1800,
    same_site="lax",
    https_only=settings.session_https_only,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
app.include_router(web.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    installed = OllamaClient().installed_models()
    return {
        "status": "ok",
        "database": "ok",
        "ollama": "ok" if installed else "unavailable",
        "models": installed,
    }


@app.exception_handler(404)
def not_found(_request, _exc):
    return JSONResponse({"detail": "资源不存在"}, status_code=404)
