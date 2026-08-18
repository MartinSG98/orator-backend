from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db
from app.routers import documents, jobs, languages, translations

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Local-runtime bootstrap only. On Lambda the filesystem is read-only
    # and persistence is DynamoDB, there is nothing to create.
    if settings.runtime == "local":
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        init_db()
    yield


app = FastAPI(title="Orator API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(languages.router)
app.include_router(documents.router)
app.include_router(translations.router)
app.include_router(jobs.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
