"""bot → 渲染农场协调器的同步 HTTP 客户端。

在 `_render_html_image` 的渲染回调线程里调用（`image_reply` 经 `asyncio.to_thread` 执行），
同步阻塞没关系；无 worker 在线或失败时返回 None，上层自动回落本地 ImageRenderer。
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class FarmClient:
    def __init__(self, url, token, timeout=8.0, health_cache=3.0):
        self.base = url.rstrip("/")
        self.token = str(token)
        self.timeout = float(timeout)
        self.health_cache = float(health_cache)
        self._last_health = 0.0
        self._last_workers = 0

    def _request(self, path, payload=None, method=None):
        url = f"{self.base}{path}?token={urllib.parse.quote(self.token)}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data,
                                     method=method or ("POST" if data else "GET"))
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def any_worker(self) -> bool:
        """近期是否有 worker 在线（health 结果缓存 3 秒，避免每次渲染都打一枪）。"""
        now = time.monotonic()
        if now - self._last_health < self.health_cache:
            return self._last_workers > 0
        try:
            h = self._request("/mine/health")
            self._last_workers = int(h.get("workers", 0))
        except Exception:
            self._last_workers = 0
        self._last_health = now
        return self._last_workers > 0

    def render(self, sha, html, width, height, clip="") -> bytes | None:
        """提交一次渲染，返回 PNG bytes；无 worker / 超时 / 出错返回 None。"""
        try:
            r = self._request("/render", {
                "sha": sha, "html": html, "width": int(width),
                "height": int(height), "clip": clip,
            })
            if r.get("ok") and r.get("png_b64"):
                return base64.b64decode(r["png_b64"])
            return None
        except Exception:
            return None

    def health(self) -> dict:
        try:
            return self._request("/mine/health")
        except Exception:
            return {"ok": False, "workers": 0}
