# 读取 pptx 与视觉自检

## 读取 pptx

```bash
# 设计元数据与全部文字：主题配色字体、逐页形状样式、每段文字的字样
.keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/read_pptx.py presentation.pptx

# 原始 XML 与媒体文件
unzip -o presentation.pptx -d unpacked/
```

## 视觉自检

自检分两层，**第 1 层代码读取校验不能省**：`validate_deck.py` 对整副 deck 全量跑，报错全部修掉
（见 deck-design 的「自检」）。这一节讲第 2 层看图校验：**定向渲染，只看版面**。

复查范围 = 第 1 层报出框 / 溢出 / 文字互压的页 ∪ 建页前容量评估的高风险与临界页 ∪ 每种页型各抽一页。
范围以外的页不出图逐页扫描。

```bash
.keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/render_preview.py $WORK/deck/deck.pptx -o $WORK/deck/preview/ --slides 3,7,12
```

全量渲染（不带 `--slides`）用于读模版原件。底层就是两步，需要手动控制参数时直接用：

```bash
/Applications/LibreOffice.app/Contents/MacOS/soffice --headless \
  --convert-to pdf output.pptx
pdftoppm -jpeg -r 110 -f 3 -l 3 output.pdf slide-fixed
```

### macOS 上的中文字体

LibreOffice 自带的 fontconfig 配置不扫描 macOS 系统字体目录，中文会渲染成空白且不报错。
手动调用 soffice 时指定 Homebrew 的 fontconfig 配置：

```bash
FONTCONFIG_PATH=/opt/homebrew/etc/fonts \
  /Applications/LibreOffice.app/Contents/MacOS/soffice --headless \
  --convert-to pdf output.pptx
```

`render_preview.py` 已内置这项设置。渲染图中缺中文时，先检查这一项。

### 交给子代理看图

出图之后可以派子代理看图，派发时只给复查范围内的图和每页的预期：

```
看这些幻灯片，找出版面问题。重叠、溢出、截断优先。

查这些：
- 元素重叠：文字压文字、文字压形状、文字压画面、线穿字、几个元素叠在一起；
- 文字溢出框、被边界截断、掉出页面；文字换行后，按单行定位的装饰元素对不上；
- 页脚和正文撞在一起；
- 文本框太窄导致过度换行；
- **框大内容少**：卡片高度超过里面文字高度一倍以上，收到与内容匹配或换版式；
- 间距明显不均（一处空一大片、一处挤成一团），元素几乎贴住（小于 0.3 英寸）或离页边不足 0.5 英寸；
- 同一页几组元素、分栏、同类元素没有对齐；
- 深底深字、浅底浅字，对比度不够；
- 画面被裁剪、被拉伸，或在目标尺寸下读不清；
- 和模版原件对照：配色面积、字体、圆角、投影、版头版脚是不是同一套设计语言；

不查：错别字、术语写法、内容对错、措辞——这些在前面阶段已经定稿。
几像素级的微小偏差不用列。

依次读取并分析这些图：
1. $WORK/deck/preview/slide-03.jpg（预期：……）
2. $WORK/deck/preview/slide-07.jpg（预期：……）
```

### 收工条件

- 第 1 层 `validate_deck.py` 没有错误；
- 第 2 层复查范围内的页修完上面这些版面问题，改过的页重渲染确认一次；
- 抽查发现的系统性问题按页型批量改过、再抽一页确认。
- 不追求「反复循环到挑不出任何问题」：定向看图（渲染 → 看图 → 修复）**最多 3 轮**，
  3 轮后仍剩的版面问题记进验收报告、照常交付；用户验收后明确要求继续优化才再开新的轮次。
  第 1 层代码校验不受此限。

## 依赖

- `python-pptx`、`Pillow`、`lxml`：建 deck、注入动画、读取 pptx（装在 `.keyframe-venv` 里）；
- `LibreOffice`：pptx → PDF（`brew install --cask libreoffice`）；
- `poppler`：PDF → 图片（`brew install poppler`）。
