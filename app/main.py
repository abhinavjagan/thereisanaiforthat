"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.web import web

app = FastAPI(
    title="AI Tools Database",
    description="Aggregated database of AI tools, models, and services.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)
app.include_router(web)


@app.get("/health")
async def health():
    return {"status": "ok"}
