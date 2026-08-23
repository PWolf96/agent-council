"""FastAPI application factory + entrypoint (config-driven, poll-based)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.api.routes import follow_up, models, runs, teams


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_dotenv()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Deliberation API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(runs.router)
    app.include_router(teams.router)
    app.include_router(models.router)
    app.include_router(follow_up.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
