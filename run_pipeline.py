"""流水线入口。

用法示例：
  # 引导模式（无需 API key）：人工分片过门控，离线跑通 stage0~5 全链路
  python run_pipeline.py --pages 1-8 --stage2-from intermediate_outputs/page_analysis

  # 真实 LLM 模式（需先设置环境变量 LLM_API_KEY；可选 LLM_BASE_URL / LLM_MODEL）
  python run_pipeline.py --pages 1-8

  # 之后跑金标判卷
  python eval.py

产物全部写入 workdir/（stage0~5 分目录，可单独重跑）；
金标 final_output/ 与人工分片 intermediate_outputs/ 只读不写。
"""
import argparse

from pipeline import (config, llm_client, stage0_preprocess, stage1_layout,
                      stage2_classify, stage3_structure, stage4_merge, stage5_assemble)


def parse_pages(s):
    if not s:
        return None
    if "-" in s:
        a, b = s.split("-", 1)
        return set(range(int(a), int(b) + 1))
    return {int(s)}


def main():
    ap = argparse.ArgumentParser(description="文档结构化抽取流水线")
    ap.add_argument("--input", default=str(config.INPUT_JSONL))
    ap.add_argument("--pages", default=None, help="如 1-8；缺省全文档")
    ap.add_argument("--workdir", default=str(config.WORKDIR))
    ap.add_argument("--stage2-from", default=None,
                    help="引导模式：人工分片目录（信封格式），跳过 LLM")
    ap.add_argument("--force", action="store_true", help="忽略缓存重跑 stage2")
    args = ap.parse_args()
    pages = parse_pages(args.pages)

    print("Stage 0 预处理（纯代码）…")
    print(" ", stage0_preprocess.run(args.input, args.workdir))
    print("Stage 1 版式统计（纯代码）…")
    print(" ", stage1_layout.run(args.workdir))

    if args.stage2_from:
        print(f"Stage 2 页面分类（引导模式：{args.stage2_from}）…")
        print(" ", stage2_classify.run_from(args.stage2_from, pages, args.workdir))
    else:
        print("Stage 2 页面分类（LLM + 门控）…")
        print(" ", stage2_classify.run(pages, args.workdir, args.force))

    print("Stage 3 全局结构重建（两遍法）…")
    print(" ", stage3_structure.run(args.workdir))
    print("Stage 4 文本归属拼接…")
    print(" ", stage4_merge.run(args.workdir))
    print("Stage 5 组装 + 校验…")
    print(" ", stage5_assemble.run(args.workdir))

    usage = llm_client.usage_summary()
    if usage:
        print("\nLLM 用量汇总（模型 / 调用 / 成功 / 输入tok / 输出tok / 总耗时s / 失败）:")
        for row in usage:
            print("  ", row)
    print("\n完成。产物在 workdir/stage5/final.json；判卷：python eval.py")


if __name__ == "__main__":
    main()
