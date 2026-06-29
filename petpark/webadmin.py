"""宠物乐园专属管理网站。

在 AstrBot 进程内启动一个独立端口的 aiohttp 网站，提供：
- 账号密码登录（默认 admin / 2468080asd，可在插件配置修改）；
- 查看 / 增删改查 插件数据库（玩家 players、群设置 groups、卡密 cards）；
- 批量生成卡密（金币 / 积分 / 钻石）。

依赖 aiohttp（AstrBot 自带）。启动失败不会影响插件主体功能。
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from astrbot.api import logger

COOKIE = "pp_session"
TABLES = ("players", "groups", "cards")


class WebAdmin:
    def __init__(self, store, host: str, port: int, user: str, password: str):
        self.store = store
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self._tokens: set[str] = set()
        self._runner = None

    # --------------------------- 生命周期 ---------------------------
    async def start(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/login", self._login_page)
        app.router.add_post("/login", self._login_submit)
        app.router.add_get("/logout", self._logout)
        app.router.add_post("/api/list", self._api_list)
        app.router.add_post("/api/upsert", self._api_upsert)
        app.router.add_post("/api/delete", self._api_delete)
        app.router.add_post("/api/cards/generate", self._api_gen_cards)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._runner = runner
        logger.info(
            f"[petpark] 管理网站已启动: http://{self.host}:{self.port} "
            f"(账号 {self.user})"
        )

    async def stop(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
            self._runner = None

    # --------------------------- 鉴权 ---------------------------
    def _authed(self, request) -> bool:
        return request.cookies.get(COOKIE) in self._tokens

    def _require(self, request):
        from aiohttp import web

        if not self._authed(request):
            raise web.HTTPFound("/login")

    async def _login_page(self, request):
        from aiohttp import web

        return web.Response(text=LOGIN_HTML, content_type="text/html")

    async def _login_submit(self, request):
        from aiohttp import web

        data = await request.post()
        if (
            data.get("user") == self.user
            and data.get("password") == self.password
        ):
            token = secrets.token_hex(16)
            self._tokens.add(token)
            resp = web.HTTPFound("/")
            resp.set_cookie(COOKIE, token, httponly=True, max_age=86400)
            return resp
        return web.Response(
            text=LOGIN_HTML.replace("<!--ERR-->", "账号或密码错误"),
            content_type="text/html",
        )

    async def _logout(self, request):
        from aiohttp import web

        tok = request.cookies.get(COOKIE)
        self._tokens.discard(tok)
        resp = web.HTTPFound("/login")
        resp.del_cookie(COOKIE)
        return resp

    async def _index(self, request):
        from aiohttp import web

        self._require(request)
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    # --------------------------- API ---------------------------
    @staticmethod
    def _table(name: str) -> str:
        if name not in TABLES:
            from aiohttp import web

            raise web.HTTPBadRequest(text="未知数据表")
        return name

    def _json(self, payload: Any):
        from aiohttp import web

        return web.json_response(payload, dumps=lambda o: json.dumps(o, ensure_ascii=False))

    async def _api_list(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        return self._json({"ok": True, "data": self.store._data.get(table, {})})

    async def _api_upsert(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        key = str(body.get("key", "")).strip()
        value = body.get("value")
        if not key:
            return self._json({"ok": False, "msg": "键不能为空"})
        if not isinstance(value, dict):
            return self._json({"ok": False, "msg": "记录内容必须是 JSON 对象"})
        self.store._data.setdefault(table, {})[key] = value
        await self.store.save()
        return self._json({"ok": True})

    async def _api_delete(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        key = str(body.get("key", ""))
        self.store._data.get(table, {}).pop(key, None)
        await self.store.save()
        return self._json({"ok": True})

    async def _api_gen_cards(self, request):
        self._require(request)
        body = await request.json()
        try:
            codes = self.store.create_cards(
                currency=body.get("currency", ""),
                amount=int(body.get("amount", 0)),
                count=int(body.get("count", 1)),
                prefix=body.get("prefix", ""),
            )
        except (ValueError, TypeError) as e:
            return self._json({"ok": False, "msg": str(e)})
        await self.store.save()
        return self._json({"ok": True, "codes": codes})


LOGIN_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宠物乐园 · 管理登录</title>
<style>
body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0f172a;color:#e2e8f0;display:flex;min-height:100vh;align-items:center;justify-content:center}
.box{background:#1e293b;padding:32px;border-radius:16px;width:320px;box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{font-size:20px;margin:0 0 20px;text-align:center}
input{width:100%;box-sizing:border-box;padding:11px;margin:8px 0;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:14px}
button{width:100%;padding:11px;margin-top:12px;border:0;border-radius:8px;background:#6366f1;color:#fff;font-size:15px;cursor:pointer}
button:hover{background:#4f46e5}
.err{color:#f87171;text-align:center;min-height:18px;font-size:13px}
</style></head><body>
<form class="box" method="post" action="/login">
<h1>🐾 宠物乐园 · 管理后台</h1>
<div class="err"><!--ERR--></div>
<input name="user" placeholder="账号" autocomplete="username">
<input name="password" type="password" placeholder="密码" autocomplete="current-password">
<button type="submit">登录</button>
</form></body></html>"""


DASHBOARD_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宠物乐园 · 管理后台</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0f172a;color:#e2e8f0}
header{background:#1e293b;padding:14px 20px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:10}
header h1{font-size:17px;margin:0;flex:1}
header a{color:#94a3b8;text-decoration:none;font-size:14px}
.tabs{display:flex;gap:8px;padding:14px 20px 0}
.tabs button{padding:8px 16px;border:0;border-radius:8px 8px 0 0;background:#1e293b;color:#94a3b8;cursor:pointer;font-size:14px}
.tabs button.active{background:#334155;color:#fff}
main{padding:16px 20px}
.bar{margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input,select{padding:8px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px}
button.act{padding:8px 14px;border:0;border-radius:8px;background:#6366f1;color:#fff;cursor:pointer;font-size:13px}
button.act:hover{background:#4f46e5}
button.del{background:#dc2626}button.del:hover{background:#b91c1c}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px;border-bottom:1px solid #334155;text-align:left;vertical-align:top}
th{color:#94a3b8;position:sticky;top:54px;background:#0f172a}
td.k{font-family:monospace;color:#a5b4fc;word-break:break-all;max-width:260px}
pre{margin:0;white-space:pre-wrap;word-break:break-all;max-width:520px;font-size:12px;color:#cbd5e1}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:20}
.modal .card{background:#1e293b;padding:20px;border-radius:14px;width:min(640px,92vw)}
textarea{width:100%;height:300px;font-family:monospace;font-size:13px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;padding:10px}
.muted{color:#64748b;font-size:12px}
.tag{padding:2px 8px;border-radius:999px;font-size:12px}
.used{background:#7f1d1d;color:#fecaca}.unused{background:#14532d;color:#bbf7d0}
</style></head><body>
<header><h1>🐾 宠物乐园 · 管理后台</h1><a href="/logout">退出登录</a></header>
<div class="tabs">
<button data-t="players" class="active" onclick="tab('players')">玩家 players</button>
<button data-t="groups" onclick="tab('groups')">群设置 groups</button>
<button data-t="cards" onclick="tab('cards')">卡密 cards</button>
</div>
<main>
<div id="cardgen" style="display:none" class="bar">
<select id="cur"><option>金币</option><option>积分</option><option>钻石</option></select>
<input id="amt" type="number" placeholder="面额" style="width:120px">
<input id="cnt" type="number" placeholder="数量" value="1" style="width:90px">
<input id="pre" placeholder="前缀(可选)" style="width:120px">
<button class="act" onclick="genCards()">批量生成卡密</button>
<span id="genout" class="muted"></span>
</div>
<div class="bar">
<button class="act" onclick="addRow()">＋ 新增记录</button>
<input id="q" placeholder="搜索关键字…" oninput="render()" style="flex:1;min-width:160px">
<button class="act" onclick="load()">刷新</button>
</div>
<div id="tablewrap"></div>
</main>
<div class="modal" id="modal"><div class="card">
<h3 id="mtitle">编辑记录</h3>
<input id="mkey" placeholder="键(key)" style="width:100%;margin-bottom:8px">
<textarea id="mval"></textarea>
<div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end">
<button class="act" onclick="closeModal()" style="background:#475569">取消</button>
<button class="act" onclick="saveRow()">保存</button>
</div></div></div>
<script>
let cur='players', cache={};
function tab(t){cur=t;document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));document.getElementById('cardgen').style.display=(t==='cards')?'flex':'none';load();}
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return r.json();}
async function load(){const r=await api('/api/list',{table:cur});cache=r.data||{};render();}
function render(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const k of Object.keys(cache)){
  const v=cache[k];
  if(q && !(k.toLowerCase().includes(q)||JSON.stringify(v).toLowerCase().includes(q)))continue;
  let extra='';
  if(cur==='cards'){const u=v.used;extra=`<span class="tag ${u?'used':'unused'}">${u?'已用':'未用'}</span> ${v.currency} +${v.amount}<br>`;}
  rows+=`<tr><td class="k">${esc(k)}</td><td>${extra}<pre>${esc(JSON.stringify(v,null,1))}</pre></td>
  <td style="white-space:nowrap"><button class="act" onclick='editRow(${JSON.stringify(k)})'>编辑</button>
  <button class="act del" onclick='delRow(${JSON.stringify(k)})'>删除</button></td></tr>`;
 }
 document.getElementById('tablewrap').innerHTML=`<table><thead><tr><th>键</th><th>内容</th><th>操作</th></tr></thead><tbody>${rows||'<tr><td colspan=3 class=muted>暂无数据</td></tr>'}</tbody></table>`;
}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function editRow(k){openModal(k,cache[k]);}
function addRow(){openModal('',{});}
function openModal(k,v){document.getElementById('mkey').value=k;document.getElementById('mkey').readOnly=!!k;document.getElementById('mval').value=JSON.stringify(v,null,2);document.getElementById('modal').style.display='flex';}
function closeModal(){document.getElementById('modal').style.display='none';}
async function saveRow(){
 const k=document.getElementById('mkey').value.trim();
 let v;try{v=JSON.parse(document.getElementById('mval').value);}catch(e){alert('JSON 格式错误: '+e);return;}
 const r=await api('/api/upsert',{table:cur,key:k,value:v});
 if(!r.ok){alert(r.msg||'保存失败');return;}
 closeModal();load();
}
async function delRow(k){if(!confirm('确认删除 '+k+' ?'))return;await api('/api/delete',{table:cur,key:k});load();}
async function genCards(){
 const body={currency:document.getElementById('cur').value,amount:+document.getElementById('amt').value,count:+document.getElementById('cnt').value,prefix:document.getElementById('pre').value};
 const r=await api('/api/cards/generate',body);
 if(!r.ok){alert(r.msg||'生成失败');return;}
 document.getElementById('genout').textContent='已生成 '+r.codes.length+' 张：'+r.codes.join('  ');
 load();
}
load();
</script></body></html>"""
