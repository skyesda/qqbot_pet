# 国庆贺词图文卡

采用内置 GPT imagegen 生成底图，程序排版真实贺词、作者、主题、奖励与点赞编号。
素材：`petpark/assets/ui/national-day.webp`（约 338 KB，内嵌图片，无外部图片请求）。
模板：`petpark/moonfest/card.py`。运行 `python tools/preview_national_day.py` 生成示例预览。

## 多人展示

`月华墙` 默认第一页：热度最高 5 条 + 最新 10 条。
`月华墙 2` 查看更早的 10 条，尾页显示余下条目。
墙面编号始终是全服倒序序号，最新为 1，和 `点赞 编号` 一致。
新投稿会使旧条目的序号后移，点赞前应查看最新墙面。
投稿成功卡提示当时的编号 1；图片示例均为虚构数据。

## 审核与回退

先由本地词表拦截明确违规，再由 Jev 同一次请求判断合规性与节庆祝福主题。
支持国庆、中秋和节庆亲友祝福；不适内容、广告、游戏闲聊、低置信度判断不得上墙。
Jev 禁用或请求失败时提示稍后重试，不写墙、不扣次数、不发奖励。
主题契合奖励仍沿用既有 Jev/本地关键词判定，不作为上墙审核的替代。
图片渲染失败只回退文字，不重复执行游戏写入与奖励。
历史已存在的祝福沿用原数据，本次严格审核针对新投稿。

## 生成提示词

Create a premium Chinese National Day greeting stationery background for a Chinese fantasy cultivation game, portrait aspect ratio 2:3. Elegant traditional hand-painted illustration on textured warm ivory rice paper. Restrained cinnabar red and antique gold, deep pine green accents. Top 24 percent: atmospheric layered Chinese mountain silhouettes with a flowing red silk ribbon and delicate small golden fireworks in the upper corners, festive yet refined, artistic flat ink and mineral pigment painting, not 3D. Center 60 percent: very spacious nearly blank warm ivory paper, only extremely faint cloud patterns, suitable for overlaying readable Chinese greeting text. Bottom 16 percent: small traditional rooftops, distant mountains and wisps of gilded clouds, low contrast. Thin delicate gold framing lines at the edges. Excellent craft, editorial print quality, no generic AI glowing fantasy, no loud gradients, no people, no logos, absolutely no text, no letters, no numbers, no calligraphy. Full bleed rectangular artwork.
