"""Stage 0 预处理（纯代码，不调 LLM）。

输入 input/*.rows.jsonl → 输出 workdir/stage0/pages/page_N.json：
  按页分组、行按 y 排序、页眉/页脚/正文/图形文字角色标记、空白页判定。

角色判定 = 频率统计（v3，替代正则硬编码，适用于任意文档类型）：
  1) 相对坐标：y_rel = y / 本页最大 y。规避各页坐标系尺度不一致；
  2) 频率扫描：遍历所有 page 的 line 记录，统计每段文本出现在多少页；
     出现页数 / 总页数 ≥ FREQ_THRESHOLD（默认 0.8）的文本 → 页眉页脚候选；
  3) 位置聚类：候选文本按平均 y_rel 分为"顶部组"（<0.35）和"底部组"（>0.60）；
     顶部组 → header_texts，底部组 → footer_texts；
  4) 打角色：line 记录的 text 在 header_texts 且 y_rel ≤ header_band → header；
     在 footer_texts 且 y_rel ≥ footer_band → footer；否则 body。
  字号完全不参与角色判定（page 8 顶部 size=9 延续正文的陷阱，见 Agent.md 3）。

figure 记录处理（v3）：抽取器把部分正文文本（目次/前言/引言/附录标题、部分注释与
  定义尾部）放进了 t='figure' 记录的 text 字段。本版将其按 \\n 拆行、以
  role='figure_text' 纳入页面行流（figure 记录自带 y，可参与排序），stage1 打标题
  候选、stage2 单独传递给 LLM 归类——不再丢弃。is_blank 仍要求 line 记录与 figure
  记录均为零（纯图片页不算文本空白页）。
"""
import json
import re
from collections import Counter
from pathlib import Path

from . import config

_HDR_REGION, _FTR_REGION = 0.35, 0.75   # 位置聚类的"上部/下部"粗分界
_HDR_FALLBACK, _FTR_FALLBACK = 0.10, 0.90  # 候选样本不足时的回退带
FREQ_THRESHOLD = 0.8   # 文本出现在 ≥80% 的页面 → 页眉页脚候选


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def _band_upper(samples):
    """眉头带上界：候选 95 分位 × 1.5，上封 0.35；样本 <3 回退默认。"""
    s = sorted(samples)
    if len(s) < 3:
        return _HDR_FALLBACK
    return min(0.35, _quantile(s, 0.95) * 1.5)


def _band_lower(samples):
    """页脚带下界：候选 5 分位 × 0.9，限制在 [0.75, 0.95]；样本 <3 回退默认。"""
    s = sorted(samples)
    if len(s) < 3:
        return _FTR_FALLBACK
    return max(0.75, min(0.95, _quantile(s, 0.05) * 0.9))


def _detect_header_footer_texts(pages, page_max_y):
    """频率统计法：扫描全部页面，找出跨页高频出现的页眉/页脚文本。

    返回 (header_texts: set, footer_texts: set, header_band, footer_band,
           freq_header_cand, freq_footer_cand)
    """
    total = len(pages)
    if total == 0:
        return set(), set(), _HDR_FALLBACK, _FTR_FALLBACK, 0, 0

    # 统计每段文本出现在哪些页，以及平均相对位置
    text_pages = {}        # text → set of page numbers
    text_y_rels = {}       # text → list of y_rel values

    for n, p in pages.items():
        my = page_max_y.get(n) or 0.0
        seen_this_page = set()
        for ln in p["lines"]:
            if ln["src"] != "line":
                continue
            t = ln["text"]
            r = (ln["y"] / my) if my else 0.0
            text_pages.setdefault(t, set()).add(n)
            text_y_rels.setdefault(t, []).append(r)
            seen_this_page.add(t)

    # 高频文本 = 出现在 ≥ FREQ_THRESHOLD 页面的文本
    threshold = max(2, int(total * FREQ_THRESHOLD))
    header_cands = []   # (text, avg_y_rel)
    footer_cands = []
    for t, pset in text_pages.items():
        if len(pset) < threshold:
            continue
        avg_rel = sum(text_y_rels[t]) / len(text_y_rels[t])
        if avg_rel <= _HDR_REGION:
            header_cands.append((t, avg_rel))
        elif avg_rel >= _FTR_REGION:
            footer_cands.append((t, avg_rel))
        # 中间区域的高频文本（如"注："开头的重复注释）不做角色判定，按 body 处理

    header_texts = {t for t, _ in header_cands}
    footer_texts = {t for t, _ in footer_cands}

    header_band = _band_upper([r for _, r in header_cands]) if header_cands else _HDR_FALLBACK
    footer_band = _band_lower([r for _, r in footer_cands]) if footer_cands else _FTR_FALLBACK

    return (header_texts, footer_texts, header_band, footer_band,
            len(header_cands), len(footer_cands))


def line_role(text: str, y_rel: float,
              header_texts: set, footer_texts: set,
              header_band: float, footer_band: float) -> str:
    """给定相对位置与高频文本集合，判定单行角色（仅用于 line 记录）。"""
    if y_rel <= header_band and text in header_texts:
        return "header"
    if y_rel >= footer_band and text in footer_texts:
        return "footer"
    return "body"


def run(input_path=None, workdir=None) -> dict:
    input_path = Path(input_path or config.INPUT_JSONL)
    root = Path(workdir or config.WORKDIR)
    out_root = root / "stage0" / "pages"
    out_root.mkdir(parents=True, exist_ok=True)

    pages, order, cur = {}, [], None
    for line in input_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if o.get("t") == "page":
            cur = int(o["page"])
            pages[cur] = {"page": cur, "lines": [], "figures": 0, "raw_line_count": 0}
            order.append(cur)
        elif o.get("t") == "line" and cur is not None:
            pages[cur]["lines"].append({"y": float(o.get("y") or 0.0),
                                        "size": o.get("size"),
                                        "bold": o.get("bold"),
                                        "text": (o.get("text") or "").strip(),
                                        "src": "line"})
            pages[cur]["raw_line_count"] += 1
        elif o.get("t") == "figure" and cur is not None:
            pages[cur]["figures"] += 1
            # figure 记录的 text 是正文的一部分（标题/注释/定义续行），
            # 按 \n 拆行纳入页面流，role 固定为 figure_text
            for piece in (o.get("text") or "").split("\n"):
                piece = piece.strip()
                if piece:
                    pages[cur]["lines"].append({"y": float(o.get("y") or 0.0),
                                                "size": None, "bold": None,
                                                "text": piece,
                                                "src": "figure",
                                                "role": "figure_text"})

    for n in order:
        pages[n]["lines"].sort(key=lambda l: l["y"])

    page_max_y = {n: max((l["y"] for l in p["lines"]), default=0.0)
                  for n, p in pages.items()}

    def _rel(n, y):
        my = page_max_y.get(n) or 0.0
        return (y / my) if my else 0.0

    header_texts, footer_texts, header_band, footer_band, hdr_cnt, ftr_cnt = \
        _detect_header_footer_texts(pages, page_max_y)

    result = {}
    for n in order:
        p = pages[n]
        for ln in p["lines"]:
            if ln["src"] == "line":
                ln["role"] = line_role(ln["text"], _rel(n, ln["y"]),
                                       header_texts, footer_texts,
                                       header_band, footer_band)
        p["line_count"] = p["raw_line_count"]  # line 记录数（不含 figure 拆行）
        p["is_blank"] = p["raw_line_count"] == 0 and p["figures"] == 0
        (out_root / f"page_{n}.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        result[n] = p

    blanks = sorted(n for n, p in result.items() if p["is_blank"])
    summary = {"total_pages": len(order), "blank_pages": blanks,
               "header_band": round(header_band, 4), "footer_band": round(footer_band, 4),
               "freq_header_texts": sorted(header_texts),
               "freq_footer_texts": sorted(footer_texts),
               "figure_text_lines": sum(1 for p in result.values()
                                        for l in p["lines"] if l["role"] == "figure_text")}
    (root / "stage0" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
