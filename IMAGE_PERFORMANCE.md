# 图片响应优化与验证

本次优化覆盖菜单、宠物卡、背包、修士、地图、突破、坐骑等 `_render_html_image` HTML 卡片链路。普通远程图片、棋盘与摸金的 Pillow 绘图、QQ 平台实际发送不属于此次基准范围。

## 改动

- 启动时后台预热 Chrome；复用独立线程中的浏览器及页面，每次导航到空白文档后加载 HTML，避免页面状态串用。
- 游戏指令与数据修改仍在事件循环中执行。仅已生成的 HTML 快照进入图片工作线程，等待截图时其他消息可以继续处理。
- 同一图片在串行渲染队列内再次检查缓存，并发相同内容只生成一次。
- 缓存键包含 HTML、窗口尺寸、裁切函数名和渲染版本；临时 PNG 验证成功后原子替换目标文件。
- 每类保留至少 256 张图，最近生成/命中 24 小时内的图片不删除，减少 QQ 延迟拉图时的失效。该保留策略是软上限，高频动态生成时应监控磁盘空间。
- 标准卡片仅截图有效面板，减少大面积空白画布编码；PNG 保持无损，使用较低压缩等级减少 CPU 耗时（文件体积可能增加）。
- 浏览器故障或缺少 Playwright 时临时回退系统 Chrome CLI，60 秒后再尝试常驻浏览器；失败不会发布半成品文件。

## 部署

在实际运行机器人的 Python 环境中，从插件目录执行：

```sh
python -m pip install -r requirements.txt
# 已安装系统 Google Chrome 时无需下载浏览器；否则执行：
python -m playwright install chromium
```

可使用环境变量 `PETPARK_CHROME_PATH` 指定浏览器可执行文件。更新文件及依赖后重启插件/机器人。首次依赖加载、预热完成前以及浏览器崩溃恢复期间仍可能有秒级延迟。此次只修改本地工作区，未重启或部署线上服务。

Playwright 的线程使用限制和安装方法参见[官方文档](https://playwright.dev/python/docs/library)。所有同步 Playwright 调用固定在同一工作线程中。

## 实测（2026-09-07，本地 Windows）

真实背包模板，HTML 约 3.16 MB，原截图窗口 760×5200。测量起点是 HTML 快照已准备好，终点是 PNG 与 Markdown URL 可用。

| 场景 | 耗时 |
|---|---:|
| 原 Chrome CLI 链路，单次样本 | 1416 ms |
| 新链路冷启动，单次样本 | 1424 ms |
| 浏览器就绪后生成不同内容，5 次 | 339–396 ms |
| 同内容命中缓存，30 次中位数 | 11.0 ms |
| 同内容命中缓存，30 次 P95 | 13.3 ms |

更早的一次进程冷启动达到约 7.3 秒，说明冷启动波动明显，不能以单次样本保证 SLA。浏览器预热用于把这项成本移到启动阶段。

这些结果不包含 HTML 构造、存档、额外坐骑推送、QQ API、QQ 拉取图片、网络带宽及客户端显示时间，也不是并发负载下的 SLA。当前可证实的是缓存准备达到十几毫秒；不能据此保证端到端图片送达始终为毫秒级。

## 复测与排查

从插件父目录执行：

```sh
python -m unittest qqbot_pet.tests.test_image_renderer qqbot_pet.tests.test_adventure_map -v
python qqbot_pet/tools/benchmark_images.py output/image-performance
python qqbot_pet/tools/preview_cards.py output/image-performance/visual-check
```

7 项自动测试通过；宠物、背包、菜单、空背包、长名称背包及 100 项背包的文字溢出与四角边框检查通过，并检查了真实背包输出。

日志 `image_ready` 提供 `cache_hit` 和 `elapsed_ms`；`image_render` 提供渲染后端、耗时和图片字节数。若本地准备很快而客户端仍慢，需要在线测量发送接口耗时、服务器图片下载首字节及完整下载耗时，才能进一步确定网络或平台瓶颈。
