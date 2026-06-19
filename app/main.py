# FastAPI app entry point. Routers are registered here with their URL prefixes; this
# is the only place they are wired to the app. Schema is owned by Alembic — no
# create_all at startup.


from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers.auth import router as auth_router
from app.routers.health import router as health_router
from app.routers.text import router as text_router
from app.routers.users import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="redactcat", lifespan=lifespan)

app.include_router(health_router, prefix="/health")
app.include_router(auth_router, prefix="/auth")
app.include_router(users_router, prefix="/users")
app.include_router(text_router, prefix="/text")
