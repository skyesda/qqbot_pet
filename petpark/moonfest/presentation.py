"""QQ Markdown presentation shared by every festival command."""
import re


TITLES = {
    '拜月': '🌙 拜月祈愿', '华诞签到': '🎆 华诞签到',
    '猜灯谜': '🏮 月下灯谜', '喂玉兔': '🐇 玉兔小憩',
    '做月饼': '🥮 月饼匠心', '重制': '🥮 月饼重制',
    '酿桂花': '🍶 桂花酿', '取酒': '🍶 开坛取酒',
    '玉兔同行': '🐇 玉兔同行', '贺词': '🎆 山河祝福',
    '点赞': '🎆 祝福共赏', '巡礼': '🎪 华诞巡礼',
    '献礼': '🎁 万家献礼', '双庆': '🎊 双节同庆',
    '月华商店': '🌙 月华商店', '买卡': '🌙 月华商店',
    '月华信息': '🌙 月华档案', '我的月华': '🌙 月华档案',
    '月华档案': '🌙 月华档案', '月华榜': '🌙 月华榜',
    '月华墙': '🌙 月华祝福墙', '里程碑': '🏮 群里程碑',
    '活动帮助': '🌙 月耀华诞 · 活动指令',
}


def markdown_reply(command, text):
    if text is None or not text.strip():
        return text
    # Deferred image jobs must remain bare markers; already rendered cards are
    # complete replies. Do not alter their Markdown URLs or dimension syntax.
    if text.startswith('![') or text.startswith('__PETPARK_IMAGE_'):
        return text
    paragraphs, table = [], []

    def flush_table():
        if table:
            paragraphs.append('\n'.join(table))
            table.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('|') and line.endswith('|'):
            table.append(line)
            continue
        flush_table()
        if not line:
            continue
        if re.fullmatch(r'【[^】]+】', line):
            line = '### ' + line[1:-1]
        elif line.startswith('——') and line.endswith('——'):
            line = '> ' + line.strip('— ').strip()
        elif line.startswith('· '):
            line = '- ' + line[2:]
        if line.startswith('- '):
            words = line[2:].split(' ', 1)
            if words[0] in TITLES:
                line = '- **' + words[0] + '**' + (' ' + words[1] if len(words) > 1 else '')
        # Reward badges stay readable in the plain-text fallback too.
        line = re.sub(r'(?<!\*)【月华 ×\d+】(?!\*)', lambda m: '**' + m[0] + '**', line)
        paragraphs.append(line)
    flush_table()
    body = '\n\n'.join(paragraphs)
    if not body.startswith('## '):
        body = '## ' + TITLES.get(command, '🌙 月耀华诞') + '\n\n' + body
    return body
