"""National Day stationery; all player content is escaped before rendering."""
import base64
from functools import lru_cache
from html import escape
from pathlib import Path


@lru_cache(maxsize=1)
def _background():
    path = Path(__file__).parents[1] / 'assets' / 'ui' / 'national-day.webp'
    return 'data:image/webp;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def _esc(value):
    return escape(str(value), quote=True)


def _page(title, subtitle, content, footer, kind='greeting'):
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;width:720px}body{background:#ff00ff}
.card{width:720px;position:relative;background:#f5efe2 url("''' + _background() + '''") top center/100% auto no-repeat;
border:2px solid #bd995d;color:#183a34;padding:0 42px 30px;font-family:"KaiTi","Noto Serif CJK SC","SimSun",serif}
.masthead{padding-top:288px;text-align:center;height:448px}
.eyebrow{font-size:16px;letter-spacing:6px;color:#725335}
h1{font-size:58px;font-weight:normal;letter-spacing:8px;margin:20px 0 12px;color:#9c372b}
.subtitle{font-size:19px;letter-spacing:3px;color:#654c34}
.seal{display:inline-block;border:1px solid #a94432;color:#a94432;font-size:15px;padding:5px 10px;letter-spacing:2px}
.blessing{text-align:center;padding:32px 20px 28px;min-height:350px;background:rgba(250,246,235,.83);border:1px solid #d6c4a0}
.quote{font-size:38px;line-height:1.7;letter-spacing:3px;overflow-wrap:anywhere;margin:22px 0 24px;white-space:pre-wrap}
.signature{font-size:20px;color:#756754;overflow-wrap:anywhere;line-height:1.6}
.details{margin:22px 0 0;padding:18px;background:rgba(250,246,235,.9);border-top:1px solid #ccb994;display:flex;justify-content:space-between;gap:16px;font-size:18px;line-height:1.7}
.details strong{font-size:24px;color:#a04430}.details span{overflow-wrap:anywhere}
.foot{text-align:center;margin-top:28px;padding:12px;background:rgba(250,246,235,.86);font-size:17px;line-height:1.8;color:#5b6554}
.foot b{color:#a04430;font-weight:normal}.wall-section{margin:0 0 22px;padding:22px;background:rgba(250,246,235,.96);border:1px solid #d6c4a0}
h2{margin:0 0 16px;font-size:26px;font-weight:normal;letter-spacing:3px;color:#9c372b}
.entry{display:flex;gap:18px;border-top:1px solid #e0d5bf;padding:17px 0}.entry:last-child{padding-bottom:0}
.number{flex:0 0 38px;color:#ab4733;font-size:26px}.words{flex:1;min-width:0}
.text{font-size:26px;line-height:1.55;overflow-wrap:anywhere;white-space:pre-wrap}
.byline{display:flex;justify-content:space-between;gap:15px;font-size:17px;color:#79715d;margin-top:8px;overflow-wrap:anywhere}
.byline span:first-child{min-width:0}.likes{color:#a04430;flex-shrink:0}
.wall{background-image:none}.wall:before{content:"";position:absolute;inset:0 0 auto;height:448px;
background:url("''' + _background() + '''") top center/100% auto no-repeat;pointer-events:none}
.wall .masthead{position:relative}
</style></head><body><main class="card ''' + kind + '''"><header class="masthead">
<div class="eyebrow">灵契仙途 · 月耀华诞</div><h1>''' + _esc(title) + '''</h1>
<div class="subtitle">''' + _esc(subtitle) + '''</div></header>''' + content + '<footer class="foot">' + footer + '</footer></main></body></html>'


def greeting_html(text, name, date, theme, amount, bonus, index):
    reward = f'月华 +{int(amount)}'
    extra = f' · 契合加成 +{int(bonus)}' if bonus else ''
    content = ('<section class="blessing"><div class="seal">祝福已上墙</div>'
               f'<div class="quote">{_esc(text)}</div>'
               f'<div class="signature">{_esc(name)} 敬贺<br>{_esc(date)}</div></section>'
               '<div class="details"><span>今日主题<br>' + _esc(theme or '山河同庆') + '</span>'
               '<span>本次获得<br><strong>' + reward + '</strong>' + extra + '</span></div>')
    return _page('山河同庆', '一笺寄心意 · 万家共此时', content,
                 f'月华墙第 <b>{int(index)}</b> 笺 · 发送「月华墙」共赏祝福<br>为这条祝福点赞：<b>点赞 {int(index)}</b>')


def wall_html(hot, latest, theme, page=1, pages=1, total=None):
    def section(title, entries):
        rows = []
        for index, item in entries:
            rows.append('<article class="entry"><div class="number">' + str(int(index)) + '</div>'
                        '<div class="words"><div class="text">' + _esc(item.get('text', '')) + '</div>'
                        '<div class="byline"><span>' + _esc(item.get('name', '无名修士')) + '</span>'
                        '<span class="likes">赞 ' + str(int(item.get('likes', 0) or 0)) + '</span></div></div></article>')
        return '<section class="wall-section"><h2>' + title + '</h2>' + ''.join(rows) + '</section>'
    content = (section('众心所寄 · 最受欢迎', hot) if hot else '') + section('新笺入卷 · 最新上墙', latest)
    total = len(latest) if total is None else int(total)
    navigation = f'第 {int(page)} / {int(pages)} 页 · 共 {total} 条祝福'
    if page < pages:
        navigation += f' · 下一页：<b>月华墙 {int(page) + 1}</b>'
    if page > 1:
        navigation += f' · 上一页：<b>月华墙 {int(page) - 1}</b>'
    return _page('月华祝福墙', theme or '山河锦绣 · 祝福共赏', content,
                 navigation + '<br>发送 <b>点赞 编号</b>，为心仪的祝福添一份月华<br>写下你的心意：<b>贺词 祝福内容</b>', 'wall')
