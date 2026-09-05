# 棋盘底座素材

- 生成工具：内置 GPT Image 生图（image_gen），2026-09-05。
- 项目交付：`wood-base.png`，五子棋/中国象棋共用的俯视木质底座。
- 棋线、坐标、棋子、楚河汉界和最近一步标记由 Pillow 按规则绘制。

最终生成提示词：

> Use case: stylized-concept. Asset type: production background texture for a Chinese chat-bot board game, shared by Gomoku and Chinese chess. Create a square, perfectly top-down flat wooden board base, warm pale honey wood center with subtle fine grain, elegant dark walnut narrow frame around all four edges, small traditional Chinese carved corner accents confined to outermost 5 percent. Center 88 percent blank even wood for precise program-drawn grid and pieces. Soft even lighting, premium tactile board game look, no perspective, no chess pieces, no grid lines, no text, no symbols, no watermark. Output one square image.

字体：`BoardGlyphs.otf` 为 Noto Sans CJK SC 的精简子集并另命文件名，用于棋子和坐标。上游来源： https://github.com/notofonts/noto-cjk/tree/main/Sans ，授权见同目录 `FONT-LICENSE.txt`。仅保留棋盘实际使用的中英文字符，避免依赖部署系统字体。

## 扫雷底座 · minesweeper-base.png

使用内置 GPT Image 生图生成，已接入扫雷实际棋盘渲染；精简字体同时包含扫雷标题、状态和说明文字。

最终提示词：

> Use case: stylized-concept. Asset type: production background base for a polished Minesweeper chat game. A square perfectly flat top-down game tray in a refined botanical expedition aesthetic, deep midnight teal outer surface, subtle fine-grained matte texture, thin brushed antique brass double-line border inset at 3 percent of the canvas. Only the outermost corners have tiny tasteful fern engravings and brass rivets; interior 90 percent is plain dark teal with extremely subtle ambient gradient so a precise software-drawn grid and status cards can be composited clearly. Modern premium board game product illustration, soft even lighting, restrained jewel green and warm gold. No lettering, no numbers, no grid, no tiles, no bombs, no flags, no perspective, no watermark. One square bitmap.
