# 宠物乐园 · 项目开发与部署指南

> 本项目：`astrbot_plugin_petpark`（宠物乐园）  
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

## 三、服务端部署到 AstrBot

> **部署规则**：代码修改完成后，默认只执行 **① `git push` 推送到远程仓库** 和 **② 在服务器插件目录执行 `git pull`**。不要自行执行 SSH kill / setsid 重启 / 进程查找等其它命令。生效方式优先通过 AstrBot 后台「重载插件」完成；只有用户明确要求时，才使用命令行重启。

### 3.1 服务器信息（当前环境）

- 服务器 IP：`103.38.83.146`
- 登录用户：`root`
- 插件路径：`/root/AstrBot/data/plugins/astrbot_plugin_petpark`
- 数据文件：`/root/AstrBot/data/plugin_data/astrbot_plugin_petpark/petpark.json`
- AstrBot 根目录：`/root/AstrBot`
- Python 虚拟环境：`/root/AstrBot/.venv/bin/python3`
- 日志文件：`/root/AstrBot/astrbot.log`

### 3.2 登录服务器

```bash
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@103.38.83.146
```

### 3.3 拉取最新代码

```bash
cd /root/AstrBot/data/plugins/astrbot_plugin_petpark
git pull
```

### 3.4 生效方式：通过 AstrBot 后台重载（默认）

`git pull` 完成后，插件代码已更新，但运行中的 AstrBot 仍加载旧版本。必须由用户或管理员在 AstrBot 后台手动重载插件：

1. 打开 AstrBot 管理面板。
2. 进入「插件 / Extensions」。
3. 找到「宠物乐园」，点击「重载 / Reload」。
4. 观察日志确认无报错。

> 不要自行通过 SSH 执行 kill / setsid 等命令重启 AstrBot。

### 3.5 方式 B：命令行重启 AstrBot（仅用户明确要求时使用）

> 本节内容只供参考，默认不要执行。只有当用户明确说「命令行重启」或「后台重载不可用」时才使用。

当后台重载不可用或需要彻底重启时：

```bash
# 1. 查找进程
ps -ef | grep '[p]ython3 main.py'

# 2. 记录 PID，然后终止（例如 PID 为 524117）
kill 524117

# 3. 等待进程退出后重新启动
cd /root/AstrBot
setsid .venv/bin/python3 main.py > astrbot.log 2>&1 < /dev/null &
```

> 注意：不要用 `pkill -f 'python3 main.py'`，可能误杀 SSH 客户端进程导致掉线。

### 3.6 验证是否生效

```bash
tail -n 50 /root/AstrBot/astrbot.log
```

正常应看到插件加载成功、无异常 Traceback。

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
