from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import languages

settings = get_settings()

app = FastAPI(title="Orator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(languages.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
