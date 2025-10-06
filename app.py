from os import environ
from pprint import pprint
from typing import List, Callable, Awaitable
from urllib.parse import urlparse
from pathlib import Path

from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, Request, status
from fastapi.templating import Jinja2Templates
from pymongo import MongoClient
from fastapi.responses import PlainTextResponse, FileResponse

ctx = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    ctx["templates"] = Jinja2Templates("templates")

    ctx["mongo_client"] = MongoClient(
        environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/"),
        username=environ.get("MONGO_USER"),
        password=environ.get("MONGO_PASSWORD")
    )

    ctx["songs_dir"] = Path(environ.get("SONGS_DIR", "songs"))

    pprint(ctx)

    yield

    ctx["mongo_client"].close()

    ctx.clear()

def fastapi_serve(dir: str, ref: str, indexes: List[str] = ["index.html", "index.htm"]) -> Response:
    url_path = urlparse(ref or "/").path
    root = Path(dir)

    try_files = []

    if url_path.endswith("/"):
        try_files += [root / url_path.lstrip("/") / i for i in indexes]

    try_files += [root / url_path]

    try_files_tried = [t for t in try_files if t.is_file()]

    print(try_files, try_files_tried)

    if not try_files_tried:
        return PlainTextResponse("指定されたファイルが見つかりません", status.HTTP_404_NOT_FOUND)

    path = try_files_tried[0]

    print(path, "をサーブ中")

    return FileResponse(path)

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def cors_handler(req: Request, call_next: Callable[[Request], Awaitable[Response]]):
    res = await call_next(req)

    if req.url.path.startswith("/api/"):
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Credentials"] = "true"
        res.headers["Access-Control-Allow-Methods"] = "*"
        res.headers["Access-Control-Allow-Headers"] = "*"

        if req.method == "OPTIONS":
            res.status_code = status.HTTP_200_OK

    return res

@app.get("/upload/")
async def upload(req: Request):
    res = ctx["templates"].TemplateResponse(req, "upload.html", {
        "total": ctx["mongo_client"].taiko.songs.count_documents({}),
        "total_files": len(list(ctx["songs_dir"].rglob("*")))
    })
    res.headers["Cache-Control"] = f"public, max-age=60, s-maxage=60"
    res.headers["CDN-Cache-Control"] = f"max-age=60"
    return res

@app.get("/upload/{ref:path}")
async def static_upload(ref: str = None):
    res = fastapi_serve("static", ref)
    res.headers["Cache-Control"] = f"public, max-age=3600, s-maxage=3600"
    res.headers["CDN-Cache-Control"] = f"max-age=3600"
    return res
