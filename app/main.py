from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import campaigns, health, leads, webhooks
from app.core.config import get_settings
from app.db.redis import redis_client
from app.db.session import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    yield
    # Shutdown — clean up connections
    await engine.dispose()
    await redis_client.aclose()


app = FastAPI(
    title="AI Outreach System",
    description="Campaign automation backend powered by Smartlead",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(health.router)
app.include_router(campaigns.router, prefix="/api/v1")
app.include_router(leads.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "service": "AI Outreach System",
        "version": "0.1.0",
        "docs": "/docs",
    }
