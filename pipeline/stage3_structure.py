"""Stage 3 全局结构重建（两遍法，代码为主）。

Pass A 汇编 TOC：按页序拼接各页目录分片，编号驱动重建层级——
  续页首条编号为 X.Y 且本页无父节点 X 时，自动挂到前一页最后开放节点 X 的
  children（Agent.md 5.2 归并规则），从根上解决目录跨页问题。
Pass B 对齐：分片 sections 找到的标题 × TOC 期望标题 → 缺失清单
  （缺失 = 定向重查线索，即 Reflect 的正确用法）、章节页码区间。
"""
import json
import re
from pathlib import Path

from . import config

NUM_TOP = re.compile(r"^(\d+)\s+\S")
NUM_CHILD = re.compile(r"^(\d+)\.(\d+)(\.\d+)?\s")
FIXED = ("前言", "引言", "参考文献")


def _depth(title: str) -> int:
    t = title.strip()
    if t in FIXED or re.match(r"^附录[A-Z]", t) or NUM_TOP.match(t):
        return 0
    m = NUM_CHILD.match(t)
    if m:
        return 2 if m.group(3) else 1
    if re.match(r"^\d+\.\d+", t):
        return 1
    return 1  # 无编号标题（如前言下的归纳性子标题）按子级处理，避免混入顶层序列


def _flatten(items, depth0=0, out=None):
    out = [] if out is None else out
    for it in items:
        out.append(it["title"])
        _flatten(it.get("children", []), depth0 + 1, out)
    return out


def stitch_toc(fragment_pages):
    """fragment_pages: [(page, items)] 按页序。扁平化后按编号深度重建树。"""
    flat = []
    for page, items in fragment_pages:
        for t in _flatten(items):
            flat.append({"page": page, "depth": _depth(t), "title": t})
    root, stack = [], []
    for e in flat:
        node = {"title": e["title"], "children": [], "toc_pages": [e["page"]]}
        while stack and stack[-1]["depth"] >= e["depth"]:
            stack.pop()
        (stack[-1]["node"]["children"] if stack else root).append(node)
        stack.append({"depth": e["depth"], "node": node})
    return root


def run(workdir=None) -> dict:
    root = Path(workdir or config.WORKDIR)
    s2 = root / "stage2" / "page_analysis"
    files = sorted(s2.glob("page_*.json"), key=lambda p: int(p.stem.split("_")[1]))

    frag_pages, found = [], []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("table_of_contents"):
            frag_pages.append((d["page"], d["table_of_contents"]["items"]))
        if d.get("sections"):
            for t in _flatten(d["sections"]):
                found.append((d["page"], t))

    toc_tree = stitch_toc(frag_pages)

    expected = []
    def _toc_titles(nodes):
        for nd in nodes:
            expected.append(nd["title"])
            _toc_titles(nd["children"])
    _toc_titles(toc_tree)

    found_titles = {t for _, t in found}
    missing = [t for t in expected if t not in found_titles]
    extra = [t for t in found_titles if t not in set(expected)]

    tops = [(pg, t) for pg, t in found if _depth(t) == 0]
    spans = []
    for i, (pg, t) in enumerate(tops):
        end = tops[i + 1][0] if i + 1 < len(tops) else pg
        spans.append({"title": t, "start_page": pg, "end_page": end})

    structure = {"toc_tree": toc_tree, "toc_source_pages": [pg for pg, _ in frag_pages],
                 "expected_headings": expected,
                 "found_headings": [t for _, t in found],
                 "missing_headings": missing, "extra_headings": extra,
                 "targeted_requery": missing, "section_spans": spans}
    (root / "stage3").mkdir(parents=True, exist_ok=True)
    (root / "stage3" / "structure.json").write_text(
        json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"toc顶层节点": len(toc_tree), "toc缺失标题": missing,
            "found超出TOC": extra, "章节区间数": len(spans)}
