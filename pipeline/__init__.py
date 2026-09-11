"""文档结构化抽取流水线（workflow 架构：确定性代码编排 + LLM 局部节点）。

六段流水线，每段是一个可独立重跑的纯函数：
  stage0 预处理（纯代码）→ stage1 版式统计（纯代码）→ stage2 页面分类（LLM+门控）
  → stage3 结构重建（两遍法）→ stage4 拼接 → stage5 组装+校验
产物约定见 Agent.md 第 3/5 节；schema 唯一真源在 schemas/ 目录。
"""
