# 金标说明（final.json）

最后更新：2026-09-02

## 覆盖范围

- 本金标为部分标注的示例集：sections 完整覆盖第 7～8 页（1 范围 ～ 3.9），第 9～40 页正文与附录尚未标注，扩充中。
- table_of_contents.items 已完整（2026-09-02 由 page_3 + page_4 分片按 Agent.md 5.2 归并规则拼接：9.2～9.8 挂到节点 9 的 children，10、11、附录 A～D、参考文献为顶层条目）。分片见 `../intermediate_outputs/page_analysis/page_3.json` 与 `page_4.json`。

## 关键事实

- blank_pages: [2]：第 2 页是全文档 40 页中唯一零文本行页面，由代码确定性判定（不依赖 LLM）。
- table_of_contents.page: [3, 4]：目次实际跨第 3、4 两页（9.2 起在第 4 页），由 [3] 修正。
- sections 未包含前言/引言：page_5/page_6 分片将其建模为 sections，金标补全时需裁决是否纳入（建议纳入，与中间产物一致）。

## 标注约定

- sections.title = 正文标题行原文：术语条目（3.x）的标题行只有编号，术语名（如"个人信息 personal information"）并入 text 首行；普通章节标题行为"编号 + 中文标题"（如 5.1）。
- table_of_contents.items[].title = 去掉点线引导与页码后的条目原文。
- children 约定：TOC 节点一律带 children；sections 顶层节点带 children（可为空数组），叶子子节点可省略 children（评测时缺省视为 []，text 缺省视为 ""）。
- other_info：分片层 page_1.json 有，文档层金标暂未输出，是否纳入 final 待定。

## 已知数据陷阱（来自源 jsonl 抽取层，分片忠实记录）

- 3.2 正文在 page 7 抽取中于"身心健"处中断（"康受到损害或歧视性待遇等的，属于个人敏感信息。"疑似漏行），page 8 顶部的延续文本 size=9 与页眉同字号。分片记录页内忠实文本；合并产物（spread / 金标）为语义修复后的完整文本。分片与合并产物的差异本身就是要诊断的对象。
