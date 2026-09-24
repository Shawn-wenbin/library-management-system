from fastapi import APIRouter

from app.api.deps import SessionDep
from app.services.health import check_database

router = APIRouter(tags=["健康检查"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(session: SessionDep) -> dict[str, str]:
    await check_database(session)
    return {"status": "ready"}
