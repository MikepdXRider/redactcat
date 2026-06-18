# FastAPI application entry point — wires up lifespan, middleware, and routers
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import Base, engine
from app.modules.auth import router as auth_router
from app.modules.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="redactcat", lifespan=lifespan)

app.include_router(health_router, prefix="/health")
app.include_router(auth_router, prefix="/auth")
