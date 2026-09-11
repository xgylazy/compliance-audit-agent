"""Stage 4 文本归属拼接（代码为主）。

输入 stage2 分片 → 输出 workdir/stage4/sections.json（section 树）与 spreads.json。
归并规则（Agent.md 5.2）：
  - 分片 sections 按页序并入累积树；节点层级由标题编号驱动（X 顶层、X.Y 一层、
    X.Y.Z 两层、无编号归入前一节点之下），跨页维护"开放栈"——
    上一页的 3 术语和定义 在本页遇到 3.3 时仍是挂载父节点；
  - 同层同名节点 → 合并（text 换行拼接、pages 并集）；
  - may_be_last_page_semantics_text（跨页尾巴）在本页新节点并入【之前】，
    归属文档序最后一个已打开的 section，中文直连缝合（如注 3 的"或滥|用"），
    并把本页并入其 pages；
  - 跨页 section 写 spread 记录（合并决策可审计）。
"""
import json
import re
from pathlib import Path

from . import config

_NUM_TOP = re.compile(r"^\d+\s+\S")
_NUM_SUB = re.compile(r"^(\d+(?:\.\d+)+)")
_FIXED = ("前言", "引言", "参考文献")


def _title_depth(title: str):
    """编号可判定层级：X→0、X.Y→1、X.Y.Z→2、固定独立标题→0；无法判定→None。"""
    t = title.strip()
    if t in _FIXED or re.match(r"^附录[A-Z]", t) or _NUM_TOP.match(t):
        return 0
    m = _NUM_SUB.match(t)
    if m:
        return m.group(1).count(".")
    return None


def run(workdir=None) -> dict:
    root = Path(workdir or config.WORKDIR)
    s2 = root / "stage2" / "page_analysis"
    files = sorted(s2.glob("page_*.json"), key=lambda p: int(p.stem.split("_")[1]))

    tree = []          # 顶层累积树
    stack = []         # 开放栈：[(depth, node)]，跨页持久
    opened = []        # 文档序打开的节点引用（父先于子）

    def attach(node, depth, page):
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent_children = stack[-1][1]["children"] if stack else tree
        hit = next((x for x in parent_children if x["title"] == node["title"]), None)
        if hit is None:
            hit = {"title": node["title"], "pages": [], "text": "", "children": []}
            parent_children.append(hit)
        for p in node.get("pages", [page]):
            if p not in hit["pages"]:
                hit["pages"].append(p)
        txt = node.get("text", "")
        if txt:
            hit["text"] = (hit["text"] + "\n" + txt) if hit["text"] else txt
        opened.append(hit)
        stack.append((depth, hit))
        for ch in node.get("children", []):
            cd = _title_depth(ch["title"])
            attach(ch, depth + 1 if cd is None else cd, page)

    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        page = d["page"]
        tail_target = opened[-1] if opened else None  # 上一页延续的归属对象（先于本页新节点）
        if d.get("sections"):
            for nd in d["sections"]:
                depth = _title_depth(nd["title"])
                attach(nd, 0 if depth is None else depth, page)
        tail = d.get("may_be_last_page_semantics_text")
        if tail and tail_target is not None:
            tail_target["text"] = (tail_target["text"] or "") + tail  # 中文直连缝合
            if page not in tail_target["pages"]:
                tail_target["pages"].append(page)

    spreads = []
    def walk(nodes):
        for nd in nodes:
            if len(nd["pages"]) > 1:
                spreads.append({"title": nd["title"], "pages": nd["pages"],
                                "merge_rule": "text_direct_concat + pages_union"})
            walk(nd["children"])
    walk(tree)

    out = root / "stage4"
    out.mkdir(parents=True, exist_ok=True)
    (out / "sections.json").write_text(
        json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "spreads.json").write_text(
        json.dumps(spreads, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"顶层section数": len(tree), "跨页spread数": len(spreads)}
