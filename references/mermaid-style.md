# mermaid 图的写法与配色

所有 Markdown 模版（`bili-document-builder/references/templates/` 下）共用这一份。换一批素材直接套用，
不要把当前主题的专有内容写进来。

**mermaid 有自己的一套配色**，不跟 deck 主题走：图要在一屏里把角色按**色相**讲清楚。
做法是「**饱和填充 + 同色深一档描边 + 白色文字**」——蓝、青绿、紫、黄、橙、红各代表一类角色，
色相本身就是语义，填充压得住背景、白字在投影上也读得清（黄色偏亮，配深色字）。
**每一类角色都有色相**：不含白色块，也不含灰色块——白块在浅色页面上与底色糊在一起，
灰块在一堆彩色块里看着像没上色。
所有 Markdown 交付物共用这一份色板，换风格只改这里
（[retint_mermaid.py](../scripts/retint_mermaid.py) 会把新色板刷到所有块上）。

## 主题声明

每个 mermaid 块的第一行固定是这个声明，它把 mermaid 自带的主题变量换成整套主题的色板：

```
%%{init: {'theme':'base','themeVariables':{'primaryColor':'#3498DB','primaryTextColor':'#FFFFFF','primaryBorderColor':'#2980B9','secondaryColor':'#16A085','tertiaryColor':'#D6E3F3','lineColor':'#7F8C8D','textColor':'#2C3E50','clusterBkg':'#D6E3F3','clusterBorder':'#8FAED6','edgeLabelBackground':'#7FB3D5','fontSize':'15px','fontFamily':'Hiragino Sans GB, Helvetica Neue, Arial, sans-serif','actorBkg':'#3498DB','actorBorder':'#2980B9','actorTextColor':'#FFFFFF','signalColor':'#7F8C8D','signalTextColor':'#2C3E50','noteBkgColor':'#FDEBD0','noteBorderColor':'#E67E22','noteTextColor':'#2C3E50'}}}%%
```

`theme:'base'` 是必须的——只有 base 主题才允许逐项覆盖变量。`fontFamily` 里
**不写 Microsoft YaHei**（macOS 默认没有该字体）；`actorBkg` 等几项是 `sequenceDiagram` 专用，
其余图型用不到也不报错。

**底与标签也取色，不取白、不取灰。** `clusterBkg`（子图底）与 `tertiaryColor`（次级面）
取浅蓝面板 `#D6E3F3`，子图描边 `#8FAED6`，避免白底与浅色页面混在一起、子图标题不可见；
`secondaryColor` 取青绿 `#16A085`，不留灰色底。

`edgeLabelBackground`（箭头标签底）取中蓝 `#7FB3D5`，深色字对比度 4.9 : 1。
**这一处不用浅色**：`#DDE8F8` 这类浅蓝在页面上仍呈现为白块。

`noteTextColor` 显式写成深色 `#2C3E50`：Note 条底是浅橙 `#FDEBD0`，mermaid 默认的白字在其上不可读。
白色只用于饱和色块上的文字（蓝、青绿、绿、橙、紫、红底配白字）。

## 逐角色配色

声明之后、节点定义之后，用 `classDef` 定义角色，再用 `class 节点 cls角色` 挂上去。
**一份图里同一个角色只定义一次**，节点靠 `class` 语句归属于它：

**通用角色**（任何图都能用）：

| 角色 | 用途 | 填充 | 描边 | 文字 |
| --- | --- | --- | --- | --- |
| `clsStep` | 普通步骤、流程节点，默认角色 | `#3498DB` | `#2980B9` | `#FFFFFF` |
| `clsStore` | 存储、缓存、数据、外部状态 | `#16A085` | `#117A65` | `#FFFFFF` |
| `clsDecide` | 判断、分支条件 | `#F1C40F` | `#F39C12` | `#333333` |
| `clsActor` | 主要执行者、当前讨论的焦点 | `#9B59B6` | `#8E44AD` | `#FFFFFF` |
| `clsGood` | 收益、正向结果 | `#2ECC71` | `#27AE60` | `#FFFFFF` |
| `clsBad` | 代价、瓶颈、错误、失败路径 | `#E74C3C` | `#C0392B` | `#FFFFFF` |
| `clsKey` | 关键结论、要记住的那个节点 | `#E67E22` | `#D35400` | `#FFFFFF` |

**语义角色**（讲架构与流程时按名选，比通用角色更贴题）：

| 角色 | 用途 | 填充 | 描边 | 文字 |
| --- | --- | --- | --- | --- |
| `task` | 任务节点 | `#3498DB` | `#2980B9` | `#FFFFFF` |
| `subtask` | 子任务、分支处理 | `#2ECC71` | `#27AE60` | `#FFFFFF` |
| `router` | 路由、决策器 | `#9B59B6` | `#8E44AD` | `#FFFFFF` |
| `orchestrator` | 编排器、调度者 | `#9B59B6` | `#8E44AD` | `#FFFFFF` |
| `worker` | 工作者、执行单元 | `#F1C40F` | `#F39C12` | `#333333` |
| `generator` | 生成器 | `#2ECC71` | `#27AE60` | `#FFFFFF` |
| `evaluator` | 评估器、校验者 | `#E67E22` | `#D35400` | `#FFFFFF` |
| `merge` | 聚合、投票、汇总 | `#16A085` | `#117A65` | `#FFFFFF` |
| `source` | 原始输入、起点 | `#F1C40F` | `#F39C12` | `#333333` |
| `output` | 最终输出、结果 | `#E74C3C` | `#C0392B` | `#FFFFFF` |

**色相本身就是语义**，所以别按位置轮换配色：

- 一张图里通常只用 **2–4 个角色**，把真正对立的两三个节点区分开就够，全上色等于没上色；
- `clsGood` / `clsBad` 要**成对出现**才有对照意义；
- 一张图里同一个色相出现两次以上，说明它不再表示「这一类」了，回去合并同类节点。

节点文字用中文并加引号（`S["调度"]`），换行用 `<br/>`；一张图只讲一件事，
节点控制在 3–9 个。

## 按内容选图型

| 内容 | 用哪种 |
| --- | --- |
| 先后顺序、流水线、生命周期 | `flowchart LR` / `flowchart TD` |
| 两个角色来回交互（客户端↔服务端、主↔子智能体） | `sequenceDiagram` |
| 请求状态迁移（排队→运行→结束） | `stateDiagram-v2` |
| 概念之间的包含、并列、层级 | `flowchart` / `graph` |
| 时间轴上的阶段与并行 | `gantt` |

`classDef` 只对 `flowchart` / `graph` / `stateDiagram` 这类图型生效；
`sequenceDiagram` 和 `gantt` 不支持 `classDef`，靠上面的主题变量着色即可，
不要在它们里面写 `classDef` 或 `class` 语句。

## 收尾纪律

mermaid 块最常见的坏法不是语法写错，而是**正文被吃进了图里**：图写完了没收围栏，
后面那段解释文字就落进了块内，渲染端直接报
`Lexical error on line N. Unrecognized text.`——而且它只给图内的相对行号，
很难倒推回文件里的哪一行。

- `classDef` / `class` 是块里的最后一批语句，**它们之后紧跟的就是收尾围栏**；
- 图注、公式、追问、以及「这里要不要简化一下」这类临时念头，一律写在围栏**外面**；
- 块里不留任何笔记或待办，mermaid 不认识它们，整张图会直接挂掉；
- `stateDiagram` 的 `note ... end note` 区间里可以写多行自由文本，这是语法允许的，
  但区间必须有 `end note` 收口，漏了就一路吃到块尾。

校验脚本会在交付前拦一道（见 [bili-2-ppt/scripts/mermaid_lint.py](../scripts/mermaid_lint.py)），
但它是兜底不是许可——写的时候就按上面收口。

`mermaid_lint.py` 查的是**围栏位置和正文污染**，不查 mermaid 语法本身：箭头写法、`subgraph`
有没有闭合、`classDef` 的参数格式它都不认。要确认图真能画出来，交付前跑一次真渲染器：
把 md 里的 ` ```mermaid ` 块抽出来，在一个本地页面上用 mermaid 的 `parse()` 逐个过一遍
（页面从 CDN 取 mermaid，用后台浏览器打开，读回结果即可，不需要安装 mermaid CLI 或 Puppeteer），
以此找出渲染报错的块。
