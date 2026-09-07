"""渲染农场：把 bot 的 HTML 卡片截图外包给全网「挖矿」工，缓解服务器单机渲染慢。

三层：

- Coordinator（运行在服务器，随 bot 启动）：收 bot 的 /render 渲染请求，
  排队 + 派发给外部 worker，缓存结果；对 internet 提供 /mine/* 供 worker 拉取。
- Worker（rendermine，用户放到其它电脑上跑的 exe）：pull 任务 → 用本机 Chrome
  headless 截图 → 回传 PNG。
- Client（bot 侧同步 HTTP）：`PETPARK_FARM_URL` + `PETPARK_FARM_TOKEN` 开箱即用；
  无 worker 在线时自动回落本地 ImageRenderer。

安全：所有端点 token 校验；worker 只渲染 HTML 字符串（不渲染 URL、不开 JS 之外能力），
coordinator 只存字节、从不执行 worker 内容；有大小上限与超时重派。
"""

__all__ = ["Coordinator", "FarmClient", "start_in_thread", "worker_main"]
