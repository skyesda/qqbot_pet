# 灵契仙途 · 项目开发与部署指南

> 本项目：`astrbot_plugin_petpark`（灵契仙途）  
> 仓库：`https://github.com/skyesda/qqbot_pet`  
> 适用：AstrBot QQ 群聊宠物养成插件

---

## 一、项目结构

```
qqbot_pet/
├── main.py                 # 插件主入口，指令路由、业务逻辑
├── metadata.yaml           # 插件元数据（名称/版本/作者/repo）
├── _conf_schema.json       # AstrBot 插件配置面板 Schema
├── README.md               # 用户-facing 功能说明
├── CLAUDE.md               # 本文件：开发与部署指南
├── docs/                   # 扩展文档
│   ├── 深渊秘境.md
│   └── knowledge_base.md   # 完整功能知识库（投给 AI 使用）
├── petpark/                # 核心模块
│   ├── data.py             # 常量：品质、宠物、道具、副本、活动配置
│   ├── store.py            # PetStore：JSON 持久化、玩家/群/卡密/活动数据
│   ├── pet.py              # 宠物生成、属性、成长、品质升级
│   ├── webadmin.py         # aiohttp 管理后台
│   └── images.py           # 宠物图片相关
└── tools/
    └── gen_pet_images.py   # 图片生成脚本
```

---

## 二、本地开发流程

### 2.1 修改代码

- 业务逻辑集中在 `main.py`。
- 数值、物品、副本、品质等配置集中在 `petpark/data.py`。
- 持久化相关新增字段优先在 `petpark/store.py` 的 `get_player` / 默认值中补充。

### 2.2 语法检查

改完后必须执行：

```bash
python -m py_compile main.py petpark/data.py petpark/store.py petpark/pet.py petpark/webadmin.py
```

无输出即为通过。

### 2.3 版本号管理

每次发布前递增 `metadata.yaml` 中的 `version`：

```yaml
version: v1.26.30
```

建议规则：

- 小修复/数值调整：最后一位 +1（如 `v1.26.30` → `v1.26.31`）
- 新功能：第二位 +1（如 `v1.27.0`）
- 大重构：第一位 +1（如 `v2.0.0`）

### 2.4 提交与推送

```bash
git add -A
git commit -m "类型: 简要描述"
git push
```

提交消息常用前缀：

- `feat:` 新功能
- `fix:` 修复
- `rebalance:` 数值平衡
- `chore:` 版本号/配置/无关业务的小改动
- `docs:` 文档

如果网络被 reset，直接重试 `git push` 即可。

---

## 三、服务端部署到 SkyeBot 框架

> ⚠️ 框架已从 AstrBot 彻底迁移（2026-08-20），`/root/AstrBot` 已删除。以下路径均为 **SkyeBot 独立框架**，与本仓库（插件名 `astrbot_plugin_petpark` 只为兼容，实际跑在 SkyeBot 上）对应。

> **部署规则的授权边界**：默认只做 **① 本地 `git push` 推送远程仓库** 和 **② 服务器插件目录 `git pull`**，这两个是默认步骤、无需单独授权。但「对服务器写配置 / 冷重启 / 触发热重载」这类会改变**运行中进程**的服务器操作，**每次都要重新征得用户授权**——一次「授权全部执行」不延续到下一次。

### 3.1 服务器信息（当前环境）

- 服务器 IP：`103.38.83.146`，登录用户：`root`，SSH 免密
- 框架（**非 git**，`git pull` 勿做、勿覆盖 config.yaml）：`/root/petbot_framework/`（core/、compat/、main.py、config.yaml、requirements.txt）
- 插件（git 仓库，分支 `devin/petpark-plugin`）：`/root/petbot_framework/plugins/astrbot_plugin_petpark`
- 数据：`/root/petbot_framework/data/plugin_data/astrbot_plugin_petpark/petpark.json` + `data/known_groups.json`
- venv：`/root/petbot_framework/.venv`（Python 3.12.13，来自 uv）
- 日志：`/root/petbot_framework/framework.log`（**时区是 UTC**，人按北京时间 UTC+8 理解）
- 端口：`7799`（插件后台）、`8091`（渲染农场协调器）、`6185`（框架后台）

### 3.2 登录服务器

```bash
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@103.38.83.146
```

### 3.3 拉取最新代码（默认步骤②）

```bash
cd /root/petbot_framework/plugins/astrbot_plugin_petpark
git pull
```

> `git pull` 只是把新代码写到磁盘，**运行中进程仍跑内存里的旧代码**——「明明 pull 了/后台显示版本新了，但游戏行为没变」就是这原因。必须再触发下节的**热重载**或**冷重启**才真正生效。

### 3.4 生效方式一：插件热重载（仅改动 `main.py` 时可信任）

改动**只涉及 `main.py`（且不改任何子模块接口）**时，热重载有效。通过框架 HTTP API 触发（等价于框架后台 6185「机器人配置」页的「重载插件」按钮）：

```bash
JAR=$(mktemp)
curl -s -c "$JAR" -X POST http://127.0.0.1:6185/api/login \
  -H "Content-Type: application/json" -d '{"user":"admin","password":"2468080asd"}' >/dev/null
curl -s -b "$JAR" -X POST http://127.0.0.1:6185/api/plugin_reload \
  -H "Content-Type: application/json" -d '{"reload_code":true}'
rm -f "$JAR"
```

- 成功标志：返回 `{"ok":true,"data":{"reloaded":true}}` + 日志 `插件已加载：PetParkPlugin`；框架 **PID 不变**（热重载不换 PID）。

> **热重载对 `main.py` 之外的任何改动都不可靠**：`reload_package` 只重载 `astrbot_plugin_petpark.main` 及 `main.` 前缀子模块，**从不会**重载 `astrbot_plugin_petpark.petpark.*`。任何 `petpark/` 下模块（含嵌套子包、含「新增一个函数被 main 调用」）经 reload 后仍是旧对象 → 报 `AttributeError: module '...petpark.data' has no attribute 'xxx'`。所以**凡是动了 `petpark/` 下任何文件，直接整框架冷重启，别信 reload 返回 OK**。

### 3.5 生效方式二：整框架冷重启（改动 `petpark/` 下任何文件时必然要用）

判断依据：**只改 `main.py`（不改子模块接口）→ 热重载有效；改了 `petpark/` 下任何文件（data/store/pet/ai_router/adventure/zhongyuan/webadmin，直接或嵌套），或改了 main.py 与子模块之间的签名（构造/函数参数）→ 直接整框架冷重启。**

**唯一正确的冷重启方式**：

```bash
bash /root/petbot_framework/relaunch.sh
```

> `relaunch.sh` 会先 `export PETPARK_FARM_TOKEN/URL/LISTEN` 三个农场环境变量，再 kill 旧 PID + setsid 启动，并 `ss` 自动找端口 PID、打印冷却后的状态。
>
> ⚠️ **不要用裸 `setsid .venv/bin/python3 main.py`**——会丢掉农场 token → `:8091` 协调器不启动，webadmin 矿机监控显示「渲染农场未启用：未发现 PETPARK_FARM_TOKEN」。
> ⚠️ **不要用 `pkill -f 'python3 main.py'`**——正则自匹配会误杀 SSH 客户端进程导致掉线；按 PID kill。

> 影响插件**后台任务生命周期**的改动（任务引用从类属性改实例属性、cancel 逻辑等），热重载清不掉已在跑的旧任务，也须冷重启。

### 3.6 验证是否生效

```bash
# 冷重启后三端口都应变 LISTEN
ss -tlnp | grep -E ':(7799|8091|6185)'
# 最近日志无 Traceback；热重载后从 reload 时间戳往后 grep
grep -a "插件已加载\|Traceback" /root/petbot_framework/framework.log | tail -20
# 确认版本已是目标版本（发布前递增 metadata.yaml 的 version）
grep -m1 '^version:' /root/petbot_framework/plugins/astrbot_plugin_petpark/metadata.yaml
```

正常应看到插件加载成功、无异常 Traceback。

### 3.7 发布闭环（一图流）

```
本地：改代码 → 递增 metadata.yaml version → py_compile → git commit → git push
服务器：git pull → 判断：
   只改 main.py 且改子模块接口? → 否 → 热重载 (3.4)
                                 → 是 → 冷重启 bash relaunch.sh (3.5)
验证：3.6 三端口 LISTEN + 日志无 Traceback + 版本号正确
```

---

## 四、管理后台

插件启动后会自动开启 aiohttp 管理网站：

- 地址：`http://服务器IP:7799`
- 默认账号：`admin`
- 默认密码：`2468080asd`

后台功能：玩家管理、群设置、卡密生成、活动配置、Boss 复活等。

---

## 五、常见问题

### 5.1 SSH 连不上

现象：`Connection closed by remote host`

处理：

- 检查服务器是否正常运行、SSH 服务是否启动。
- 等待几分钟后重试。
- 若持续失败，通过服务器控制台/VNC 登录排查。

### 5.2 `git push` 被拦截或网络 reset

处理：

- 直接重试 `git push`。
- 确认本地网络或 GitHub 访问正常。
- 若仍失败，可让有权限的人代为 push。

### 5.3 插件重载后指令不生效

处理：

- 确认 `git pull` 后本地代码已更新到目标 commit。
- 确认重载时无报错。
- 必要时完整重启 AstrBot。

### 5.4 数据异常或丢失

- 玩家数据全部存在 `petpark.json`，修改前建议备份。
- 不要随意手动编辑 JSON，除非清楚字段含义。

---

## 六、扩展开发提示

- 新增指令必须加入 `main.py` 的 `KNOWN_COMMANDS` 集合，否则会被过滤。
- 日常活动指令通过 `data.DAILY_ACTIONS` 动态放行。
- 限时活动指令通过 `store.active_events()` 动态计算，无需写死在 `KNOWN_COMMANDS`。
- 新增物品需在 `petpark/data.py` 的 `ITEMS` 中定义，必要时在 `_apply_effect` 中解释其 `effect`。
- 新增副本/剧情/深渊事件等配置优先放在 `petpark/data.py`，保持 `main.py` 只负责逻辑。
