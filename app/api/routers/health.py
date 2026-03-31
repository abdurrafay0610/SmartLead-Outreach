from fastapi import APIRouter
from sqlalchemy import text

from app.db.redis import redis_client
from app.db.session import async_session_factory
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Check database and Redis connectivity."""
    db_status = "unhealthy"
    redis_status = "unhealthy"

    # Check database
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            db_status = "healthy"
    except Exception:
        pass

    # Check Redis
    try:
        await redis_client.ping()
        redis_status = "healthy"
    except Exception:
        pass

    overall = "healthy" if db_status == "healthy" and redis_status == "healthy" else "degraded"
    return HealthResponse(status=overall, database=db_status, redis=redis_status)
