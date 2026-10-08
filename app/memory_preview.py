"""Serve actual stored memories without starting cognition or migrating data.

python -m app.memory_preview --db data/eva.db --vault . --port 8767
"""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api_routes.routes_memory_graph import router


def create_app(db_path: Path, vault: Path) -> FastAPI:
    if not db_path.is_file():
        raise ValueError("memory database does not exist")
    ui = Path(__file__).resolve().parents[1] / "ui/web"
    templates = Jinja2Templates(directory=str(ui / "templates"))
    app = FastAPI(title="EVA · 只读记忆图谱")
    app.state.container = SimpleNamespace(store=SimpleNamespace(db_path=db_path.resolve()),
                                          tiered_memory=None, settings=SimpleNamespace(base_dir=vault.resolve()))
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=ui / "static"), name="static")

    @app.get("/")
    @app.get("/memory")
    def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html",
                                          context={"app_name": "EVA", "read_only_preview": True})

    @app.get("/chat", response_class=HTMLResponse)
    @app.get("/settings", response_class=HTMLResponse)
    @app.get("/memory/list", response_class=HTMLResponse)
    def runtime_required(request: Request):
        return templates.TemplateResponse(request=request, name="service_unavailable.html",
                                          context={"app_name": "EVA", "read_only_preview": True})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.db, args.vault), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
