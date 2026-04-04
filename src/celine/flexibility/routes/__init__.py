from fastapi import FastAPI
from fastapi.responses import JSONResponse

from celine.flexibility.api.commitments import router as commitments_router


def register_routes(app: FastAPI) -> None:
    @app.get("/health", include_in_schema=False)
    async def health():
        return JSONResponse({"status": "ok"})

    app.include_router(commitments_router)
