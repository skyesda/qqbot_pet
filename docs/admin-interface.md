# 管理后台界面与分页（v3.16.2）

后台与登录页沿用官网的墨绿、米白、朱砂和仙山素材。导航按日常管理、玩家服务、活动运营、系统维护分组；窄屏折叠导航，宽表横向滚动。

## 数据与加载

- 玩家、群、卡密、活动、网页账号、定制审核、定制宠物、定制坐骑、反馈、审计流水和异常标记均固定每页 10 条，服务端筛选后分页。
- 通用列表请求接受 `page`、`q`；响应包括 `data`、`total`、`page`、`size`、`pages`。审计接口仍用 `items`。非法页码回到第一页，超过末页时回到末页。
- `/api/list` 的 `key` 参数只读取指定记录的最新内容，编辑继续使用原有乐观锁检查。
- 卡密统计覆盖全表；`export: "unused"` 导出全部未使用卡密；勾选批量操作只针对当前页。
- 群推送和盛典配置表每页显示 10 条。配置输入保留在 DOM 中，翻页不会丢失未保存的输入。
- 搜索防抖 220ms；切换栏目取消旧请求，阻止过期结果覆盖新页。枚举数据按需读取，定制宠物/坐骑并行加载，图片延迟加载。
- CSS、JS 拆为静态资源，使用版本化 URL；不引入新的前端框架。

## 验证

运行环境需项目已有的 aiohttp、AstrBot 或框架兼容模块。

```powershell
python -m py_compile main.py petpark/data.py petpark/store.py petpark/pet.py petpark/webadmin.py petpark/admin_paging.py
node --check petpark/webstatic/admin.js
python -m unittest discover -s tests -p test_webadmin_pagination.py -v
```

分页回归用合成记录验证 10/10/7 分页、跨页搜索、末页收缩、单条编辑快照、全量导出、全表统计、审核/反馈筛选、定制列表、审计、无读写副作用和认证拒绝。

本地浏览器以合成数据验收桌面与 390px 手机布局、登录必填校验、列表末页、搜索、编辑弹窗、定制双列表和推送翻页。真实写操作与生产环境响应时间不在本地预览中模拟。

部署按 CLAUDE.md 执行：本次改动涉及 petpark 子模块，服务器拉取代码后需经本次授权冷重启才能全部生效。
