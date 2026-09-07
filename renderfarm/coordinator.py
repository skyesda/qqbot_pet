"""渲染农场协调器（运行在 bot 所在服务器，随插件启动）。

HTTP 端面：
- POST /render            (bot → coord)  按 sha 去重排队并等待 worker 结果，或命中缓存直接返回。
- GET  /mine/next         (worker → coord) 领取最早排队任务；无任务返回 job=null。
- POST /mine/finish       (worker → coord) 回传 PNG；成功缓存 <sha>.jpg。
- GET  /mine/health       (bot/worker → coord) 在线 worker 数、队列深等。

安全：所有端面要求查询参数 `token` 与守护一致；worker 只上传/下载字节，
coordinator 从不执行 worker 内容；HTML/PNG 均有大小上限；领取后超租期自动重派。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
import uuid
from pathlib import Path

from aiohttp import web

log = logging.getLogger("petpark.renderfarm.coordinator")

DEFAULT_MAX_HTML = 2 * 1024 * 1024      # 2MB
DEFAULT_MAX_PNG = 8 * 1024 * 1024       # 8MB
DEFAULT_RENDER_TIMEOUT = 12.0           # bot 等待一次渲染的最长秒
DEFAULT_LEASE = 40.0                    # worker 领取后无回报则回收重派


class _Job:
    __slots__ = ("id", "sha", "html", "width", "height", "clip", "lease_expire", "fut")

    def __init__(self, sha, html, width, height, clip):
        self.id = uuid.uuid4().hex
        self.sha = sha
        self.html = html
        self.width = width
        self.height = height
        self.clip = clip
        self.lease_expire = 0.0          # 0 表示尚未被任何 worker 领取
        self.fut = None                  # 仅当有 bot 在等时存在


class Coordinator:
    def __init__(self, token, cache_dir, max_html=DEFAULT_MAX_HTML, max_png=DEFAULT_MAX_PNG,
                 render_timeout=DEFAULT_RENDER_TIMEOUT, lease=DEFAULT_LEASE):
        self.token = str(token)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_html = max_html
        self.max_png = max_png
        self.render_timeout = render_timeout
        self.lease = lease
        self.queue: list[_Job] = []
        self.inflight: dict[str, _Job] = {}
        self.by_sha: dict[str, str] = {}
        self.workers: dict[str, float] = {}
        self.tasks = 0                   # 累计完成渲染数
        self._reaper = None

    def _authorized(self, request) -> bool:
        return request.query.get("token") == self.token

    # ---------------- bot 端：提交渲染并等待 ----------------
    async def handle_render(self, request):
        if not self._authorized(request):
            return web.json_response({"ok": False, "reason": "forbidden"}, status=403)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "reason": "bad_json"}, status=400)
        sha = str(data.get("sha", ""))[:64]
        html = str(data.get("html", ""))
        width = int(data.get("width", 760))
        height = int(data.get("height", 5200))
        clip = str(data.get("clip", ""))
        if not sha or not html:
            return web.json_response({"ok": False, "reason": "missing_sha_or_html"}, status=400)
        if len(html.encode("utf-8", "ignore")) > self.max_html:
            return web.json_response({"ok": False, "reason": "html_too_large"}, status=413)
        cache = self.cache_dir / f"{sha}.jpg"
        if cache.is_file() and cache.stat().st_size >= 1000:
            return web.json_response(
                {"ok": True, "from_cache": True,
                 "png_b64": base64.b64encode(cache.read_bytes()).decode("ascii")})

        job = self.inflight.get(self.by_sha.get(sha, ""))
        if job is None:
            job = _Job(sha, html, width, height, clip)
            self.queue.append(job)
            self.by_sha[sha] = job.id
        if job.fut is None or job.fut.done():
            job.fut = asyncio.get_event_loop().create_future()
        try:
            png_b64 = await asyncio.wait_for(asyncio.shield(job.fut), timeout=self.render_timeout)
        except asyncio.TimeoutError:
            return web.json_response({"ok": False, "reason": "no_worker"}, status=504)
        if not png_b64:
            return web.json_response({"ok": False, "reason": "empty_render"}, status=502)
        return web.json_response({"ok": True, "png_b64": png_b64})

    # ---------------- worker 端：领任务 / 交结果 / 健康 ----------------
    async def handle_mine_next(self, request):
        if not self._authorized(request):
            return web.json_response({"ok": False, "reason": "forbidden"}, status=403)
        worker_id = request.query.get("worker") or "anon"
        self.workers[worker_id] = time.monotonic()
        while self.queue:
            job = self.queue.pop(0)
            if self.by_sha.get(job.sha) != job.id:
                continue                     # 已被取消/覆盖
            job.lease_expire = time.monotonic() + self.lease
            self.inflight[job.id] = job
            return web.json_response({"ok": True, "job": {
                "id": job.id, "sha": job.sha, "html": job.html,
                "width": job.width, "height": job.height, "clip": job.clip,
            }})
        return web.json_response({"ok": True, "job": None})

    async def handle_mine_finish(self, request):
        if not self._authorized(request):
            return web.json_response({"ok": False, "reason": "forbidden"}, status=403)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "reason": "bad_json"}, status=400)
        job = self.inflight.get(str(data.get("job_id", "")))
        if job is None:
            return web.json_response({"ok": False, "reason": "unknown_job"}, status=404)
        self.tasks += 1
        if data.get("ok"):
            try:
                raw = base64.b64decode(data.get("png_b64") or "")
            except Exception:
                raw = b""
            if 1000 <= len(raw) <= self.max_png:
                (self.cache_dir / f"{job.sha}.jpg").write_bytes(raw)
                self.inflight.pop(job.id, None)
                self.by_sha.pop(job.sha, None)
                if job.fut and not job.fut.done():
                    job.fut.set_result(base64.b64encode(raw).decode("ascii"))
                return web.json_response({"ok": True, "cached": True})
        # 失败或产物过小：终止此 job，让等待者回落本地
        self.inflight.pop(job.id, None)
        self.by_sha.pop(job.sha, None)
        if job.fut and not job.fut.done():
            job.fut.set_result("")
        return web.json_response({"ok": False, "reason": "bad_render"})

    async def handle_mine_health(self, request):
        if not self._authorized(request):
            return web.json_response({"ok": False, "reason": "forbidden"}, status=403)
        now = time.monotonic()
        alive = sorted(w for w, t in self.workers.items() if now - t < 60)
        return web.json_response({
            "ok": True, "workers": len(alive), "queue": len(self.queue),
            "inflight": len(self.inflight), "tasks": self.tasks, "workers_alive": alive[:30],
        })

    # ---------------- 后台：租期回收 / 缓存清理 ----------------
    async def _reap(self, _app):
        while True:
            await asyncio.sleep(3)
            now = time.monotonic()
            dead = [jid for jid, j in self.inflight.items()
                    if j.lease_expire and now > j.lease_expire]
            for jid in dead:
                job = self.inflight.pop(jid, None)
                if job is None:
                    continue
                self.by_sha.pop(job.sha, None)
                if job.fut and not job.fut.done():
                    job.fut.set_result("")       # 有 bot 在等 → 告知失败，回落本地
                else:
                    self.queue.insert(0, job)    # 无人等 → 重排再派
            self.workers = {w: t for w, t in self.workers.items() if now - t < 180}
            self._prune_cache()

    def _prune_cache(self):
        try:
            rows = sorted(self.cache_dir.glob("*.jpg"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            for p in rows[512:]:
                try:
                    if time.time() - p.stat().st_mtime < 86400:
                        continue                 # 24h 内不删，避免 QQ 延迟拉图
                    p.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    async def _start_reaper(self, app):
        # on_startup 是逐个 await 的：若直接把 _reap（无限循环）挂成回调，
        # runner.setup() 会被它卡住永不返回。必须 create_task 丢后台。
        app["petpark_reaper"] = asyncio.create_task(self._reap(app))

    def make_app(self):
        app = web.Application()
        app.router.add_post("/render", self.handle_render)
        app.router.add_get("/mine/next", self.handle_mine_next)
        app.router.add_post("/mine/finish", self.handle_mine_finish)
        app.router.add_get("/mine/health", self.handle_mine_health)
        app.on_startup.append(self._start_reaper)
        return app


def _loop_main(loop, runner, host, port):
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, host, port)
        loop.run_until_complete(site.start())
        log.info("[petpark] 渲染农场协调器已启动 http://%s:%s", host, port)
        loop.run_forever()
    except Exception:
        log.exception("[petpark] 渲染农场协调器启动失败")


def start_in_thread(token, cache_dir, host="0.0.0.0", port=8091, **kw) -> Coordinator:
    """以守护线程启动协调器（自建事件循环），返回 Coordinator 实例用于灰度开关等。"""
    from aiohttp import web as _web
    coord = Coordinator(token, cache_dir, **kw)
    runner = _web.AppRunner(coord.make_app())
    loop = asyncio.new_event_loop()
    threading.Thread(target=_loop_main, args=(loop, runner, host, port),
                     daemon=True, name="petpark-renderfarm").start()
    return coord
