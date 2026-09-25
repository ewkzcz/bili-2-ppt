# 读取 pptx 与视觉自检

## 读取 pptx

```bash
# 设计元数据与全部文字：主题配色字体、逐页形状样式、每段文字的字样
.keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/read_pptx.py presentation.pptx

# 原始 XML 与媒体文件
unzip -o presentation.pptx -d unpacked/
```

## 视觉自检

**默认走 `render_preview.py`**，它把 pptx 转 PDF 再转 JPEG：

```bash
.keyframe-venv/bin/python bili-2-ppt/bili-pptx/scripts/render_preview.py deck.pptx -o preview/
```

底层就是两步，需要手动控制参数时直接用：

```bash
/Applications/LibreOffice.app/Contents/MacOS/soffice --headless \
  --convert-to pdf output.pptx
pdftoppm -jpeg -r 110 output.pdf slide
```

只要重渲染改过的那几页：

```bash
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

出图之后派一个子代理逐页看图，派发时把每页的预期一起给它：

```
逐页看这些幻灯片，假设有问题，把问题找出来。

重点看：
- 元素重叠（文字压形状、线穿字、元素叠在一起）
- 文字溢出或被边界截断
- 文字换行后，按单行定位的装饰元素对不上了
- 页脚和正文内容撞在一起
- 元素间距过小（小于 0.3 英寸）或几乎贴住
- 间距不均（一处空一大片，一处挤成一团）
- 距离页面边缘不足 0.5 英寸
- 分栏或同类元素没有对齐
- 深底上的深色字、浅底上的浅色字，对比度不够
- 文本框太窄导致过度换行
- 遗留的占位内容

每一页都列出来，即使问题很小。

依次读取并分析这些图：
1. preview/slide-01.jpg（预期：封面，主题 + 一句范围说明）
2. preview/slide-02.jpg（预期：……）
```

### 循环

- 出图 → 看图 → **把问题列出来**（一条都没列出来，就再仔细看一遍）；
- 改；
- **重新渲染受影响的那几页再看一遍**——一次修改经常带出新的问题；
- 重复，直到完整走一轮挑不出新问题。

**至少要完整走一轮「改—再看」，才能说这版可以了。**

## 依赖

- `python-pptx`、`Pillow`、`lxml`：建 deck、注入动画、读取 pptx（装在 `.keyframe-venv` 里）；
- `LibreOffice`：pptx → PDF（`brew install --cask libreoffice`）；
- `poppler`：PDF → 图片（`brew install poppler`）。
