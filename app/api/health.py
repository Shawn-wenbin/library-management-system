from fastapi import APIRouter

from app.api.deps import SessionDep
from app.services.health import check_database

router = APIRouter(tags=["健康检查"])

# 服务进程还活着吗？ 它不访问数据库。即使 MySQL 暂时断开，只要应用仍能响应，就返回成功。
@router.get("/health") 
async def health() -> dict[str, str]:
    return {"status": "ok"}

# 服务现在能处理依赖数据库的请求吗？ 它执行 SELECT 1；数据库不可用时返回 503。
@router.get("/health/ready")
async def readiness(session: SessionDep) -> dict[str, str]:
    await check_database(session)
    return {"status": "ready"}

# 这样部署系统或监控工具就能分别判断：是否需要重启应用进程，以及是否应该暂时把请求送给这个实例。
# 数据库短暂故障时，通常不必因此反复重启仍正常运行的应用。
