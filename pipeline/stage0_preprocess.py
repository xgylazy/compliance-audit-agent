"""Stage 0 预处理（纯代码，不调 LLM）。

输入 input/*.rows.jsonl → 输出 workdir/stage0/pages/page_N.json：
  按页分组、行按 y 排序、页眉/页脚/正文/图形文字角色标记、空白页判定。

角色判定 = 文本模式 + 自校准页眉/页脚带（v2，替代写死的 y<100 / y>770 魔数）：
  1) 相对坐标：y_rel = y / 本页最大 y。规避各页坐标系尺度不一致——实测 p1 隐含
     页高 1754、p40 为 842，jsonl 的 yf 字段口径不一致不可直接用，故自行按页归一；
  2) 校准：收集"文本模式命中且位于页面上部 35%（眉头候选）/ 下部 40%（页脚候选）"
     的行的相对位置；
  3) 定带：眉头带上界 = 候选 95 分位 × 1.5（上封 0.35）；页脚带下界 = 候选 5 分位
     × 0.9（限制在 [0.60, 0.95]）；候选不足 3 个时回退默认 0.10 / 0.90。
     定出的带写入 summary.json，可审计；
  4) 打角色：模式命中且落在带内 → header / footer，否则 body。
  字号完全不参与角色判定（page 8 顶部 size=9 延续正文的陷阱，见 Agent.md 3）。
  已知边界：正文里孤立的纯数字行理论上可能被误判页脚（本样本未出现）。

figure 记录处理（v3）：抽取器把部分正文文本（目次/前言/引言/附录标题、部分注释与
  定义尾部）放进了 t='figure' 记录的 text 字段。本版将其按 \\n 拆行、以
  role='figure_text' 纳入页面行流（figure 记录自带 y，可参与排序），stage1 打标题
  候选、stage2 单独传递给 LLM 归类——不再丢弃。is_blank 仍要求 line 记录与 figure
  记录均为零（纯图片页不算文本空白页）。
"""
import json
import re
from pathlib import Path

from . import config

HEADER_RE = re.compile(r"GB/T [\d.]+[—-]\d{4}$")
FOOTER_RE = re.compile(r"([0-9]+|[IVXLCDM]+)$")

_HDR_REGION, _FTR_REGION = 0.35, 0.60   # 校准时"上部/下部"的粗分界
_HDR_FALLBACK, _FTR_FALLBACK = 0.10, 0.90  # 候选样本不足时的回退带


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
    """页脚带下界：候选 5 分位 × 0.9，限制在 [0.60, 0.95]；样本 <3 回退默认。"""
    s = sorted(samples)
    if len(s) < 3:
        return _FTR_FALLBACK
    return max(0.60, min(0.95, _quantile(s, 0.05) * 0.9))


def line_role(text: str, y_rel: float, header_band: float, footer_band: float) -> str:
    """给定相对位置与自校准带，判定单行角色（仅用于 line 记录）。"""
    if y_rel <= header_band and HEADER_RE.match(text):
        return "header"
    if y_rel >= footer_band and FOOTER_RE.match(text):
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

    # ---- 自校准：页内相对坐标 → 收集模式命中候选 → 定带（仅 line 记录参与）----
    page_max_y = {n: max((l["y"] for l in p["lines"]), default=0.0)
                  for n, p in pages.items()}

    def _rel(n, y):
        my = page_max_y.get(n) or 0.0
        return (y / my) if my else 0.0

    header_cand, footer_cand = [], []
    for n, p in pages.items():
        for ln in p["lines"]:
            if ln["src"] != "line":
                continue
            r = _rel(n, ln["y"])
            if r <= _HDR_REGION and HEADER_RE.match(ln["text"]):
                header_cand.append(r)
            elif r >= _FTR_REGION and FOOTER_RE.match(ln["text"]):
                footer_cand.append(r)
    header_band = _band_upper(header_cand)
    footer_band = _band_lower(footer_cand)

    # ---- 打角色（figure_text 已在分组时固定）----
    result = {}
    for n in order:
        p = pages[n]
        for ln in p["lines"]:
            if ln["src"] == "line":
                ln["role"] = line_role(ln["text"], _rel(n, ln["y"]),
                                       header_band, footer_band)
        p["line_count"] = p["raw_line_count"]  # line 记录数（不含 figure 拆行）
        p["is_blank"] = p["raw_line_count"] == 0 and p["figures"] == 0
        (out_root / f"page_{n}.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        result[n] = p

    blanks = sorted(n for n, p in result.items() if p["is_blank"])
    summary = {"total_pages": len(order), "blank_pages": blanks,
               "header_band": round(header_band, 4), "footer_band": round(footer_band, 4),
               "calibration_samples": {"header": len(header_cand),
                                       "footer": len(footer_cand)},
               "figure_text_lines": sum(1 for p in result.values()
                                        for l in p["lines"] if l["role"] == "figure_text")}
    (root / "stage0" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
