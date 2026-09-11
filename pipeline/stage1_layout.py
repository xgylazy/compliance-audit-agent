"""Stage 1 版式统计（纯代码，不调 LLM）。

读 stage0 页面布局 → 输出 workdir/stage1/pages/page_N.json（附标题候选标记）
与 workdir/stage1/layout_stats.json。
本样本实测：正文 size≈11.04、标题 size≈10.56、页眉页脚 size=9、图注 10.5。
bold 全为 false 不可依赖（Agent.md 3）；size=10.5 的图注会得弱候选分，
交由 Stage 2 的 LLM 结合 TOC 上下文裁决。
"""
import json
import re
from collections import Counter
from pathlib import Path

from . import config

NUM_RE = re.compile(
    r"^(\d+(\.\d+)*\s+\S|附录[A-Z]|前言$|引言$|参考文献$|"
    r"第[一二三四五六七八九十百]+章|[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)")

# figure 记录里的标题形态（目 次/前 言/引 言/附 录 A 均含全角或半角空格）
FIG_HEAD_RE = re.compile(r"^(目\s*次|前\s*言|引\s*言|参\s*考\s*文\s*献|附\s*录\s*[A-Z（])")


def run(workdir=None) -> dict:
    root = Path(workdir or config.WORKDIR)
    in_root = root / "stage0" / "pages"
    out_root = root / "stage1" / "pages"
    out_root.mkdir(parents=True, exist_ok=True)

    size_count = Counter()
    raw = {}
    for f in sorted(in_root.glob("page_*.json"), key=lambda p: int(p.stem.split("_")[1])):
        p = json.loads(f.read_text(encoding="utf-8"))
        raw[p["page"]] = p
        size_count.update(round(ln["size"], 2) for ln in p["lines"]
                          if ln["role"] == "body" and ln["size"])

    body_size = size_count.most_common(1)[0][0] if size_count else None
    smaller = {s: c for s, c in size_count.items() if body_size and s < body_size - 0.05}
    heading_size = max(smaller, key=smaller.get) if smaller else None

    for n, p in raw.items():
        for ln in p["lines"]:
            cand, score = False, 0.0
            if ln["role"] == "figure_text":
                # figure 内文字：标题形态（目 次/前 言/引 言/附录X）→ 强候选；
                # 注释/定义续行 → 非标题候选，但仍会作为 figure_text_lines 交给 LLM
                if FIG_HEAD_RE.match(ln["text"]) or NUM_RE.match(ln["text"]):
                    cand, score = True, 1.0
            elif ln["role"] == "body" and ln["size"] and heading_size \
                    and abs(round(ln["size"], 2) - heading_size) <= 0.06:
                cand, score = True, (1.0 if NUM_RE.match(ln["text"]) else 0.5)
            ln["heading_candidate"] = cand
            ln["heading_score"] = score
        (out_root / f"page_{n}.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

    stats = {"body_size": body_size, "heading_size": heading_size,
             "size_histogram": dict(size_count.most_common())}
    (root / "stage1" / "layout_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats
