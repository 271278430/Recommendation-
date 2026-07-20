"""推荐服务入口。

组装顺序：lifespan（启动资源）→ 中间件（trace_id）→ 异常处理器 → 路由（/api/v1 前缀）。
所有业务接口（含元数据 GET）都在 api/ 下；本文件只做组装，不放任何业务逻辑。

运行：
  cd /data/shanghui/Recommend_question
  python -m service.main          # 起 http://127.0.0.1:8800
  python -m pytest tests/         # 跑测试

从仓库根以 `python -m service.main` 启动，仓库根自动在 sys.path，
`import mastery_store`（包）和 `import service.*` 均可用，无需 sys.path hack。
"""
from fastapi import FastAPI

from .api import mastery, meta, practice, recommend, student
from .core.config import settings
from .core.deps import lifespan
from .core.middleware import TraceIdMiddleware, register_exception_handlers

app = FastAPI(title="Recommendation Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(TraceIdMiddleware)
register_exception_handlers(app)
app.include_router(student.router, prefix="/api/v1")     # /students, /students/{id}/learning-status
app.include_router(mastery.router, prefix="/api/v1")     # /students/{id}/mastery
app.include_router(practice.router, prefix="/api/v1")    # /students/{id}/practice-events
app.include_router(recommend.router, prefix="/api/v1")   # /students/{id}/recommendations
app.include_router(meta.router, prefix="/api/v1")        # /knowledge-points, /knowledge-graph/edges


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("service.main:app", host=settings.app_host, port=settings.app_port)
