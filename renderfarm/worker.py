# -*- coding: utf-8 -*-
"""rendermine —— 放到其它电脑上的「挖矿」渲染工。

它不做任何业务，只做一件事：从服务器渲染农场协调器拉取 HTML 卡片快照 →
用本机 Chrome（headless）截成 PNG → 回传。多线程并发，越多台电脑挂机整体越快。

只依赖 Python 标准库（urllib/subprocess/threading），可用 PyInstaller 打成单文件 exe。
需要本机装有 Chrome（推荐）或 Chromium/Edge。渲染的是纯 HTML 字符串，不开 JS 之外的网络能力。

用法：
    渲染农场.exe --server http://103.38.83.146:8091 --token <口令> --concurrency 4
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def log(msg: str):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def chrome_path() -> str | None:
    explicit = os.environ.get("CHROME_PATH")
    if explicit:
        return explicit
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(root, "")
        for sub, exe in (("Google/Chrome/Application", "chrome.exe"),
                         ("Microsoft/Edge/Application", "msedge.exe")):
            p = os.path.join(base, sub, exe)
            if os.path.isfile(p):
                return p
    return None


def render_cli(chrome: str, html: str, width: int, height: int,
               timeout: int = 60, javascript: bool = False) -> bytes:
    """单张：写临时 HTML → chrome --headless --screenshot → 读 PNG 字节。

    安全设计：脚本默认关闭，用一个全新的临时 user-data-dir，绝不触碰本机的真实
    Chrome 配置；禁用 JS（--blink-settings=scriptEnabled=false）与后台网络请求，
    这样即便 HTML 里被塞了代码也只会被当静态样式渲染，无法读取/外传本机任何东西。
    """
    with tempfile.TemporaryDirectory() as td:
        html_file = Path(td) / "r.html"
        png_file = Path(td) / "r.png"
        profile = Path(td) / "profile"
        html_file.write_text(html, encoding="utf-8")
        js = [] if javascript else ["--blink-settings=scriptEnabled=false"]
        subprocess.run(
            [chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--disable-dev-shm-usage", "--disable-background-networking",
             "--disable-extensions", "--no-first-run", "--no-default-browser-check",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             f"--user-data-dir={profile}", f"--window-size={width},{height}",
             f"--screenshot={png_file}", html_file.resolve().as_uri(), *js],
            timeout=timeout, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if png_file.is_file() and png_file.stat().st_size >= 1000:
            return png_file.read_bytes()
        return b""


class MineWorker:
    def __init__(self, server: str, token: str, worker_id: str, concurrency: int,
                 timeout: int = 60, poll: float = 2.0, javascript: bool = False):
        self.server = server.rstrip("/")
        self.token = urllib.parse.quote(str(token))
        self.worker_id = worker_id
        self.timeout = timeout
        self.poll = poll
        self.javascript = javascript
        self.chrome = chrome_path()
        self.stop = threading.Event()
        if not self.chrome:
            raise SystemExit("未找到 Chrome/Chromium/Edge，请安装或用 --chrome 指向可执行文件。")
        self.threads = [threading.Thread(target=self._loop, daemon=True)
                        for _ in range(max(1, int(concurrency)))]

    def _http(self, method: str, path: str, payload=None, timeout: int = 60):
        worker = urllib.parse.quote(self.worker_id)
        url = f"{self.server}{path}?token={self.token}&worker={worker}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data,
                                     method=method or ("POST" if data else "GET"))
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _loop(self):
        misses = 0
        while not self.stop.is_set():
            job = None
            try:
                r = self._http("GET", "/mine/next", timeout=30)
                job = r.get("job")
            except Exception:
                # 网络/服务器暂不可用：退避重试
                time.sleep(min(5, 1 + misses))
                misses += 1
                continue
            misses = 0
            if not job:
                time.sleep(self.poll)
                continue
            try:
                raw = render_cli(self.chrome, job["html"], int(job["width"]),
                                 int(job["height"]), self.timeout, self.javascript)
                ok = bool(raw)
                self._http("POST", "/mine/finish", {
                    "job_id": job["id"], "ok": ok,
                    "png_b64": base64.b64encode(raw).decode("ascii") if ok else "",
                }, timeout=self.timeout)
                log(f"完成一单 job={job['sha'][:8]} {'✓' if ok else '✗'} "
                    f"({len(raw)}B, 空闲={self.worker_id})")
            except Exception as exc:
                try:
                    self._http("POST", "/mine/finish", {
                        "job_id": job["id"], "ok": False, "error": str(exc),
                    }, timeout=self.timeout)
                except Exception:
                    pass
                log(f"渲染失败 job={job['sha'][:8]}: {exc}")

    def start(self):
        log(f"rendermine 已启动：worker={self.worker_id} 并发={len(self.threads)} "
            f"Chrome={self.chrome}\n目标={self.server}，Ctrl+C 停止")
        for t in self.threads:
            t.start()

    def join(self):
        try:
            while any(t.is_alive() for t in self.threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop.set()
            log("收到 Ctrl+C，正在停止…")


def worker_main(argv=None):
    ap = argparse.ArgumentParser(description="灵契仙途 · 渲染农场挖矿工")
    ap.add_argument("--server", "-s", default=os.environ.get("PETPARK_FARM_URL"),
                    help="协调器地址，如 http://103.38.83.146:8091")
    ap.add_argument("--token", "-t", default=os.environ.get("PETPARK_FARM_TOKEN"),
                    help="与协调器一致的口令")
    ap.add_argument("--concurrency", "-c", type=int,
                    default=max(1, (os.cpu_count() or 2) - 1),
                    help="并发渲染线程数（默认 CPU-1）")
    ap.add_argument("--worker", "-w", default=os.environ.get("COMPUTERNAME") or "pc",
                    help="本机标识，便于区分")
    ap.add_argument("--chrome", default=os.environ.get("CHROME_PATH"), help="Chrome 路径")
    ap.add_argument("--timeout", type=int, default=60, help="单张截图超时秒")
    ap.add_argument("--javascript", action="store_true",
                    help="让 Chrome 执行 HTML 里的 JS（默认关闭，安全起见不执行）")
    args = ap.parse_args(argv)
    if not args.server or not args.token:
        ap.error("必须提供 --server 与 --token（也可用环境变量 PETPARK_FARM_URL / PETPARK_FARM_TOKEN）")
    worker = MineWorker(args.server, args.token, args.worker,
                        args.concurrency, args.timeout, poll=2.0,
                        javascript=args.javascript)
    if args.chrome:
        worker.chrome = args.chrome
        if not Path(args.chrome).is_file():
            raise SystemExit(f"指定的 Chrome 不存在：{args.chrome}")
    worker.start()
    worker.join()


if __name__ == "__main__":
    worker_main()
