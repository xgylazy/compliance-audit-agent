# 文档结构化抽取 Agent — 问题分析与设计决策

## 1. 问题分析

1. 编号体系没有统一标准，实际文档中大量混用：
第一章 总则            ← 纯汉字
1.1 适用范围           ← 纯数字
（一）效力等级          ← 括号 + 汉字
A.1 术语说明           ← 英文 + 数字
第3章 管理要求          ← 汉字 + 阿拉伯数字
正则穷举这些形态必然脆弱，且一旦出现规则外格式就静默降级（层级信息丢失），用户毫无感知。更隐蔽的问题是正则会误命中："详见第 3 条"这类正文引用、"第一，我们要坚持……"这类序数词开头，都会被错误识别为标题。
根本原因：编号体系是文档级全局属性（1.1 的层级取决于文档里还存在哪些编号），单行正则无法判断。

2. 需要 LLM 分析，而且其中包含一些细节：
    文本内容类型：
    fulltitle：封面或者扉页上的全文标题
    table_of_contents：目录/目次
    blank_page：空白页（★ 不需要 LLM：零文本行的页面由代码在 Stage 0 确定性判定。本样本中 page 2 是全文档 40 页里唯一的空白页）
    sections：正文内容（除 fulltitle、table_of_contents 以外的文字部分）
    1）需要判断当前 page 有哪些内容，分别属于什么文本内容类型
    2）可能出现跨页的情况，样本中是跨两页，后面可能有跨 n 页的场景
    3）很可能出现一整页，或者连续几页都是纯文字、无标题的情况 —— 已解决，见第 4 节两遍法：先建全文档标题索引（TOC 锚点），再切分归属；两标题之间的页面必然属于前一个标题，不再需要"暂存猜测"
    4）大模型输出的 json 一定要满足格式：
       schema 唯一真源在 schemas/ 目录：分片层 = schemas/page_analysis.schema.json（信封格式），文档层 = schemas/final.schema.json。
       分片层信封统一为：
       {
         "page": N,
         "content_types": ["fulltitle" | "table_of_contents" | "blank_page" | "sections"],
         "fulltitle": ..., "other_info": ..., "table_of_contents": ...,
         "may_be_last_page_semantics_text": ..., "sections": ...
       }
       各类型 payload 形态：
       fulltitle：        { "fulltitle": "", "other_info": {} }
       table_of_contents：{ "table_of_contents": { "items": [ { "title": "", "children": [] } ] } }
                          （页码不进 payload；文档级汇总后才有 "page": []）
       blank_page：       无 payload（全部 payload 为 null，content_types: ["blank_page"]，由代码生成）
       sections：         { "may_be_last_page_semantics_text": "", "sections": [ { "title": "", "pages": [], "text": "", "children": [] } ] }
       每一页必须恰好一条分片记录；正文延续页 content_types 为空数组、payload 全 null。文件缺失 = 流水线 bug。

## 2. 架构选型结论

Workflow（确定性代码编排 + LLM 作局部认知节点）为主；Reflect 收缩为局部组件：schema 校验失败 → 带错误信息重试；TOC 对齐发现标题漏检 → 定向重查。ReAct / Plan-Exec 不适用：步骤完全可预知、无运行时动态规划需求、金标评测要求可复现。判断法则：能画出固定流程图 → workflow；下一步取决于运行时才能发现的中间结果 → 自主 agent。本任务属于前者。

## 3. 流水线（六段）

Stage 0 预处理（纯代码）：jsonl 按页分组、行按 y 排序；空白页判定（零文本行 → blank_page，直接生成分片，不调 LLM）；页眉页脚剥离。
Stage 1 文档级版式统计（纯代码）：字号/坐标分布 → 标题候选打分。本样本实测：正文 size≈11.04、标题 size≈10.56、页眉页脚 size=9。数据陷阱：bold 全为 false 不可依赖；size=10.5 的图注与标题字号几乎相同，需 TOC 对齐排除；page 8 顶部两行正文延续文本的 size=9 与页眉同字号，因此页眉页脚判定不能只看字号，要结合 y 范围与文本模式（页眉 = 固定眉头文本"GB/T 35273—2020"，页脚 = 纯数字或罗马数字）。
Stage 2 页面语义分类（LLM + 门控）：输入 = 本页 + 前后页预览（不是整篇文档）；输出 = page_analysis/page_N.json 信封；JSON schema 校验失败 → 带错误信息重试。空白页不进本阶段。
Stage 3 全局结构重建（两遍法核心，见第 4 节）。
Stage 4 文本归属拼接（代码为主）：正文挂树；跨页合并（含断词缝合，如注 3 的"或滥|用"）；合并决策记录到 reasoning_trajectory/spread_N.json。
Stage 5 组装 + 校验（代码）：按 schemas/final.schema.json 组装；执行 5.3 的 round-trip 页面覆盖校验。
Stage 6 评测回归：金标 diff 报告写入 intermediate_outputs/eval/。

## 4. 两遍法（对应问题 1 与 2.3）

- 第一遍（全局索引）：先抽 TOC 得到官方标题清单与层级——目录就是文档自带的金标锚点；结合版式规则给每行打标题候选分。编号体系按文档级统一，以 TOC 为权威依据。
- 第二遍（切分归属）：两标题之间的所有页面必然属于前一个标题的 section；连续 n 页无标题从"在线猜测"变成"确定性区间填充"。LLM 只在模糊地带被调用，用双重证据裁决（字号不符 + 不在 TOC 清单 → reject "详见第 3 条"这类正文引用）。
- TOC 对齐一石三鸟：类型判断、编号误命中、标题漏检（TOC 有 5.4 但正文没找到 → 定向重查，这正是 Reflect 的正确用法：校验器驱动的定向修复）。

## 5. 中间产物 schema 与归并规则

5.1 两层 schema（唯一真源：schemas/ 目录）
- 分片层 intermediate_outputs/page_analysis/page_N.json：信封格式，每页恰好一条。
- 文档层 final_output/final.json：由分片纯代码汇总而成，不经 LLM。

5.2 归并规则（map → reduce，全部纯代码）
| 内容类型 | 分片形态 | 归并规则 | 汇总字段 |
|---|---|---|---|
| blank_page | content_types 含 blank_page | 收集页码，升序去重 | blank_pages |
| fulltitle | fulltitle / other_info | 取扉页那一份；多于一份报错 | fulltitle / other_info |
| table_of_contents | 本页目录条目片段（items） | 按页序拼接；续页首条编号为 X.Y 且本页无父节点 X 时，挂到前一页最后开放节点 X 的 children | table_of_contents（page = 升序并集） |
| sections | 本页新开始的 section + may_be_last_page_semantics_text（上一 section 延续到本页开头的尾巴） | 同一 section 跨页合并：text 按序拼接（断词缝合）、pages 并集 | sections 树 |

5.3 round-trip 校验（审计闭环）
从 page_analysis 分片出发应能纯代码重建 final.json 骨架；1..N 每页必须恰好落入 fulltitle 页 / table_of_contents.page / blank_pages / sections 四个桶之一；重建失败 → 精确定位到具体页的分片。这是"中间产物用来定位问题"的兑现方式。
