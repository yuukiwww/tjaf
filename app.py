from os import environ
from pprint import pprint
from typing import List, Callable, Awaitable
from urllib.parse import urlparse
from pathlib import Path
from hashlib import sha256
from time import time_ns
from traceback import TracebackException
from shutil import rmtree

from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, Request, status, UploadFile, Form, Depends
from fastapi.templating import Jinja2Templates
from pymongo import MongoClient
from fastapi.responses import PlainTextResponse, FileResponse, JSONResponse
from nkf import nkf
from pydantic import BaseModel
from redis.asyncio import from_url
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter

from tjaf import Tja

ctx = {}

async def default_identifier(req: Request):
    cloudflare_ip = req.headers.get("CF-Connecting-IP")
    if cloudflare_ip:
        return cloudflare_ip.split(",")[0]

    forwarded = req.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0]

    return req.client.host + ":" + req.scope["path"]

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

    redis_uri = environ.get("REDIS_URI", "redis://127.0.0.1:6379/")
    redis_connection = from_url(redis_uri, encoding="utf8")
    await FastAPILimiter.init(redis_connection, identifier=default_identifier)

    yield

    ctx["mongo_client"].close()

    ctx.clear()

    await FastAPILimiter.close()

class TjafDeleteItem(BaseModel):
    id: str

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

@app.post("/api/upload")
async def upload_file(file_tja: UploadFile, file_music: UploadFile, maker: str = Form(), maker_url: str = Form()):
    try:
        # ファイルが存在しない場合
        if not file_tja.filename or not file_music.filename:
            return JSONResponse({"error":"ファイルが選択されていません"})

        # TJAファイル読み込み
        tja_raw = await file_tja.read()
        tja_data = nkf('-wd', tja_raw)
        tja_text = tja_data.decode("utf-8")
        print("TJAのサイズ:", len(tja_text))

        # TJA解析
        tja = Tja(tja_text)

        # ハッシュ生成
        msg1 = sha256()
        msg1.update(tja_data)
        tja_hash = msg1.hexdigest()
        print("TJA:", tja_hash)

        # 音楽ファイル解析
        music_data = await file_music.read()

        # 音楽ファイルもハッシュ生成
        msg2 = sha256()
        msg2.update(music_data)
        music_hash = msg2.hexdigest()
        print("音楽:", music_hash)

        # 曲ID生成
        generated_id = f"{tja_hash}-{music_hash}"

        # メーカーの前処理
        maker_id = 0

        # メーカーID作成
        if maker or maker_url:
            maker_id += 1 + ctx["mongo_client"].taiko.makers.count_documents({})

        # MongoDB用データ作成
        db_entry = tja.to_mongo(generated_id, time_ns(), maker_id)
        pprint(db_entry)

        # データベースにデータをぶち込む
        ctx["mongo_client"].taiko.songs.insert_one(db_entry)

        if maker_id:
            # メーカーのデータも作成
            db_maker_entry = {
                "id": maker_id,
                "name": maker,
                "url": maker_url
            }

            # メーカーのデータもぶち込む
            ctx["mongo_client"].taiko.makers.insert_one(db_maker_entry)

        # 保存ディレクトリ
        target_dir = ctx["songs_dir"] / generated_id
        target_dir.mkdir(parents=True, exist_ok=True)

        # ファイル保存
        (target_dir / "main.tja").write_bytes(tja_data)
        (target_dir / f"main.{db_entry['music_type']}").write_bytes(music_data)

        return {"success": True, "id": generated_id}

    except Exception as e:
        error_str = "".join(TracebackException.from_exception(e).format())
        return JSONResponse({"error": error_str})

@app.post("/api/delete", dependencies=[Depends(RateLimiter(times=1, seconds=86400))])
async def api_delete(item: TjafDeleteItem) -> Response:
    # データベースから曲消す
    ctx["mongo_client"].taiko.songs.delete_one({ "id": item.id })

    # ディレクトリを取得
    root_dir = ctx["songs_dir"]
    target_dir = root_dir / item.id

    # 親ディレクトリ自身か全く違う場合は拒否
    if root_dir.resolve() not in (target_dir.resolve().parents or []):
        return PlainTextResponse(content="このディレクトリは削除できません", status_code=status.HTTP_403_FORBIDDEN)

    # 削除を実行
    rmtree(target_dir)

    return "成功しました。"

@app.get("/upload/")
async def upload(req: Request):
    res = ctx["templates"].TemplateResponse(req, "upload.html", {
        "total": ctx["mongo_client"].taiko.songs.count_documents({}),
        "dir": ctx["songs_dir"].absolute()
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

@app.get("/delete/")
async def delete_home(req: Request, id: str):
    res = ctx["templates"].TemplateResponse(req, "delete.html", {
        "title": ctx["mongo_client"].taiko.songs.find_one({ "id": id })["title"],
        "id": id
    })
    res.headers["Cache-Control"] = f"public, max-age=60, s-maxage=60"
    res.headers["CDN-Cache-Control"] = f"max-age=60"
    return res

@app.get("/delete/{ref:path}")
async def static_delete(ref: str = None):
    res = fastapi_serve("static", ref)
    res.headers["Cache-Control"] = f"public, max-age=3600, s-maxage=3600"
    res.headers["CDN-Cache-Control"] = f"max-age=3600"
    return res
