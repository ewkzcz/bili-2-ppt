---
name: bili-pptx
description: 把画面材料重述为以真实画面为主体、带完整分步动画的学习笔记PPT。先读所选 PPT 模版原件（pptx + html）取设计语言，再按每页内容构图，用 python-pptx 建页、注入 OOXML 入场动画、校验并渲染成图做视觉自检。
---

# 从画面材料到学习笔记PPT

## 先读这四条（本阶段 token 消耗极高，必须按规范执行）

1. **不做上游的活**：文字只读字幕阶段的纠错稿和术语表，不再纠错别字、同音字，不统一术语，
   不补采画面，不改知识树分类。上游材料有小缺口就在页面上写「待补充」，**不把流程打回上游**；
2. **建页前评估容量**：立页清单时逐页估算文字量、要点数、画面数、代码与表格行数，
   提前标出会重叠的页并在建页前拆页或删减（见 deck-design 的「建页前的容量评估」）；
3. **截图整幅放入，绝不裁剪**：只用 `picture()` 等比缩放，看不清就放大区域或独占一页；
4. **自检分两层**：第 1 层 `validate_deck.py` 代码读取校验全量跑、报错全修，不能省；
   第 2 层看图只渲染复查范围内的页、只查版面，不复查错别字术语这类上游已定稿的东西，
   不逐页全量扫描（见 deck-design 的「自检」）。

这个子技能只负责**学习笔记 deck 这一种交付物**：把画面材料重述成一套能讲、能复习、
点着往下走的分步幻灯片。它不采集画面、不写 Markdown 文档。

页面该怎么排、从模版取什么，看 [references/tools/deck-design.md](references/tools/deck-design.md)；
分步动画的契约看 [references/tools/animations.md](references/tools/animations.md)；
读取 pptx、视觉自检的做法看 [references/tools/reading-and-qa.md](references/tools/reading-and-qa.md)。

## PPT 模版

模版是 `references/` 下同名的一对原件：`<模版名>.pptx` 与 `<模版名>.html`。

- **选用**：用户在开头指定模版名就用它，没指定用 **`Cryo_Academic`**；
  一份材料定了哪套模版，重出、补页都沿用那一套；
- **新增**：用户把新模版的 pptx 与 html 以同一个名字放进 `references/`，即可按名字选用，不需要改任何脚本。

模版是**主题参考**：取它的设计语言（配色角色与面积分工、字体与字号层级、形状语言、版头版脚、
识别母题与背景），不取它的示例内容，也不照搬它的页面。每页按这一页要讲的内容构图，
同一种页型要有多种变体。细则见 deck-design 的「从模版取什么」「按内容构图」。

## 读模版

做 deck 之前把所选模版读透，这一步不交给脚本归纳。本技能的命令都在 `agent-docs/Agent` 下运行：

1. **看原件**：逐页出图看版面，再读 html 里的设计说明：

   ```bash
   .keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/render_preview.py \
     bili-2-ppt/bili-pptx/references/<模版名>.pptx -o <工作目录>/template-preview/
   ```

2. **读元数据**：列出主题配色与字体、每页背景、每个形状的坐标 / 填充 / 描边 / 圆角 / 投影、
   每段文字的字体 / 字号 / 颜色 / 字距：

   ```bash
   .keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/read_pptx.py \
     bili-2-ppt/bili-pptx/references/<模版名>.pptx [--slides 1,3]
   ```

   需要更细的就 `unzip -o <模版名>.pptx -d <工作目录>/template-xml/` 解包读 XML，背景纹理图在 `ppt/media/` 下；
3. **写样式表**：把读出的设计语言写成本份 deck 的 `deck_style.py`，放在工作目录（不放进技能）：
   - `TEMPLATE`（模版名）、`FONTS`（至少 `heading` / `body` / `mono`，每个是 `{"latin", "ea"}`）；
   - 颜色、文字样式、形状样式的取值（`deck_kit` 的样式字典格式）；
   - 本份 deck 共用的页面外壳函数：开页（背景）、版头（眉标、标题、结论句、压线）、版脚（页码、进度），
     封面 / 目录 / 章节页的母题画法。页码与总页数取自页清单；
   - 字号按 deck-design 的教程下限抬档，层级比例照原件；
4. **装字体**：检查原件用到的字体，缺的能装就装：

   ```bash
   .keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/ensure_fonts.py <模版名>
   ```

## 绘图工具

`scripts/deck_kit.py` 只提供与模版无关的绘图工具，设计取值全部由样式表给出：

| 接口 | 用途 |
| --- | --- |
| `new_deck(title=, template=)` / `blank(prs)` | 建 16:9 空 deck（模版名记进文档属性，校验按它核对配色）/ 加空白页 |
| `background(slide, color= / image=)` | 纯色背景，或铺满的背景纹理图 |
| `shape(slide, x, y, w, h, style, kind=)` | 矩形 / 圆角矩形 / 椭圆：填充、透明度、渐变、描边、圆角、投影 |
| `textbox(slide, text, x, y, w, h, style)` | 文字；`**短语**` 按 `emph` 强调，段内可混排多种样式 |
| `bullets(slide, items, x, y, w, h, style, bullet=, term=)` | 要点列表，「术语：解释」的术语单独强调 |
| `line(slide, x1, y1, x2, y2, color=, pt=, tail=)` | 连线与箭头 |
| `picture(slide, image, x, y, w, h, frame=)` | 图片等比缩放居中，带相框描边 / 投影 |
| `code_block(slide, code, x, y, w, h, box=, style=, syntax=)` | 代码块，关键字 / 字符串 / 数值 / 注释着色 |
| `text_width` / `text_height` / `fit_size` | 估算文字宽高，先量再定框 |
| `save(prs, path, heading_font=, body_font=)` | 存盘，并把 pptx 主题字体设成模版字体 |

`anim=N` 把动画组号写进形状名，建完由 `inject_animations.py` 注入。

## 画面是页面的主体

**deck 必须根据画面做，不能只根据文字做。** 每一章都要有以真实画面为主体的页面：
真实运行结果、真实数据进相框当主体，旁边配结论；需要一眼看清的代码、表结构才用原生元素重建。
一页最多两张画面。细则见 deck-design 的「画面是页面的主体」。

**截图不要裁剪。** 图片版里的每一张画面都整幅放入：只用 `deck_kit.picture()` 按原始比例缩放，
不用 PIL 预先裁图，不设 `crop_*`，不拉伸变形，播放器控件与黑边也不去掉。
裁掉边缘最先丢的就是表格最右一列、代码最后一行。图里要看清的部分太小时，
放大画面区域或让这张图独占一页。只有**图形版**重绘时可以不画浏览器外壳（见「交付两个版本」），
那是重绘时的取舍，不是裁图。

## 交付两个版本

**deck 一次交两份**，同一目录，文件名只差一个后缀：

- `{主题}-图片版.pptx`：画面原样嵌入，装进相框；
- `{主题}-图形版.pptx`：页面内容里**不含任何位图**，每一处画面都按其内容重绘成原生矢量元素
  （形状、文字、表格、箭头）。

两份的页序、页数、标题、文字内容完全一致，只有画面那一块不同，并排翻就能看出哪一页重绘错了。

图形版的要求：

- 重绘**信息完整**：画面里的标题、标签、数值、表格行列、代码行、箭头方向都要在；
- **不还原讲师手写批注**：圈画、箭头旁的字、页边备注不画；
- 可以**丢掉界面外壳**：浏览器标签条、地址栏、导航栏、广告位不必还原，只画内容。

## 动画

**动画是必做项**：整副牌默认做入场级联，每个叙事单元一组，按阅读顺序依次出现。
同组号 = 同一次点击，一起出现；一页 2–5 步，超过说明该拆页。

入场动画只控制什么时候显示，元素始终在页面上，所以渲染图看到的是全部展开的完整状态——
渲染图里有重叠，放映时一定有重叠。每一页的最终状态必须能独立看懂。

## 处理流程

1. **定模版、读模版**，写好 `deck_style.py`（见「读模版」）；
2. **立页清单并评估容量**：读画面保留清单和纠错稿，产出 `pages.plan.json`——每页的页码、功能性短标题、
   要讲清的要点、依据哪些画面、材料里缺什么、打算用的版式，以及 `budget`（字数、要点、画面、
   代码行、表格行）和 `risk`（高风险 / 临界 / 正常）。**高风险页在这一步就拆页或删减**，
   临界页标记为建页时先量再放，两者一起记进复查列表（见 deck-design 的「建页前的容量评估」）；
3. **分批建页**：默认 5 批，用户指定了其他数字就用指定的。契约见
   [bili-document-builder/references/transcription-fanout.md](../bili-document-builder/references/transcription-fanout.md)；
4. **合并并注入动画**：

   ```bash
   .keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/inject_animations.py deck.pptx
   ```

5. **结构校验**：

   ```bash
   .keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/validate_deck.py deck.pptx
   ```

   查禁用词、占位符残留、动画声明与形状 id 的对应、组号连续性、形状出框、中文字体、
   模版原件以外的颜色。这是**第 1 层代码读取校验**，整副 deck 全量跑，**报出的错误全部修掉**，
   修完重跑直到没有错误；它报出框、溢出、文字互压的页记进复查范围；

6. **第 2 层看图校验**：只渲染「第 1 层报出的页 ∪ 页清单复查列表 ∪ 每种页型各抽一页」
   （`render_preview.py --slides`），查重叠、溢出、截断、间距、对齐、对比度、画面被裁或读不清、
   与模版设计语言是否一致这类版面问题，改过的页重渲染确认一次。**不复查错别字、术语、内容对错**
   （上游已定稿），不逐页全量扫描，不反复循环到挑不出任何问题。做法见 reading-and-qa。

## 交付物无感

这份 pptx 必须让读者看不出内容和素材采集过程有关，也看不出经过任何工具处理。
正文、备注、形状名里都不许出现：

- 素材身份：平台名、`BV` 号、作者名、`讲师`、`合集`、`分集`、`本集`、`P03` 这类编号；
- 采集痕迹：`截图`、`关键画面`、`实拍`、`画面`、`播放器`、`字幕`、`时间点`、
  `00:53` 这类时间码、`OCR`；
- 工具痕迹：技能名、内部文件名（`pages.plan.json` 等）、`anim-` 这类内部标记；
- 谈交付物自身的话：`这份材料`、`整份材料`、`全篇`、`这一页`、`读者`、`跳着看`、`学习顺序`。

**备注是交付物的一部分**，`validate_deck.py` 用同一套词表单独查一遍。
文件名用主题命名，不带任何标识符。中间产物（样式表、页清单、分批脚本、预览图）不受此约束，
但不要放进交付目录。

## 依赖

装在 `agent-docs/Agent/.keyframe-venv` 里，用 `agent-docs/Agent/.keyframe-venv/bin/python` 运行：

- `python-pptx`、`Pillow`、`lxml`；
- LibreOffice（`brew install --cask libreoffice`）：pptx → PDF，视觉自检要用；
- poppler（`brew install poppler`）：PDF → 图片。
