"""Stage 5 组装 + 校验（纯代码）。

按 schemas/final.schema.json 组装 final.json（写入 workdir/stage5/，
绝不覆盖金标 final_output/）；执行两项校验：
  1. schema 校验（jsonschema 或内置迷你校验器）；
  2. round-trip 页面覆盖审计：每个已分析页恰好落入
     fulltitle / blank / toc / sections 四桶之一，多桶或漏桶都是 bug 信号。
"""
import json
from pathlib import Path

from . import config, gating


def _clean_toc(nodes):
    """toc 树节点只保留 title/children（满足 final.schema 的 additionalProperties=false）。"""
    return [{"title": n["title"], "children": _clean_toc(n.get("children", []))}
            for n in nodes]


def run(workdir=None) -> dict:
    root = Path(workdir or config.WORKDIR)
    s0 = json.loads((root / "stage0" / "summary.json").read_text(encoding="utf-8"))
    s3 = json.loads((root / "stage3" / "structure.json").read_text(encoding="utf-8"))
    s4 = json.loads((root / "stage4" / "sections.json").read_text(encoding="utf-8"))

    frags = {}
    for f in sorted((root / "stage2" / "page_analysis").glob("page_*.json"),
                    key=lambda p: int(p.stem.split("_")[1])):
        d = json.loads(f.read_text(encoding="utf-8"))
        frags[d["page"]] = d

    fulltitles = [d["fulltitle"] for d in frags.values() if d.get("fulltitle")]
    if len(fulltitles) != 1:
        raise ValueError(f"fulltitle 必须恰好一份，实际 {len(fulltitles)} 份")
    toc_pages = sorted(d["page"] for d in frags.values() if d.get("table_of_contents"))

    final = {
        "fulltitle": fulltitles[0],
        "table_of_contents": {"page": toc_pages, "items": _clean_toc(s3["toc_tree"])},
        "blank_pages": s0["blank_pages"],
        "sections": s4,
    }

    final_schema = json.loads(Path(config.FINAL_SCHEMA).read_text(encoding="utf-8"))
    schema_errors = gating.door2_schema(final, final_schema)

    sec_pages = set()
    def walk(nodes):
        for nd in nodes:
            sec_pages.update(nd["pages"])
            walk(nd.get("children", []))
    walk(s4)
    buckets = {"fulltitle": set(), "blank": set(s0["blank_pages"]),
               "toc": set(toc_pages), "sections": sec_pages}
    for pg, d in frags.items():
        if "fulltitle" in d.get("content_types", []):
            buckets["fulltitle"].add(pg)

    analyzed = set(frags)
    covered = set().union(*buckets.values())
    multi = sorted(p for p in analyzed if sum(p in b for b in buckets.values()) > 1)
    report = {
        "schema_errors": schema_errors,
        "multi_bucket_pages": multi,
        "uncovered_analyzed_pages": sorted(analyzed - covered),
        "covered_pages_outside_analysis": sorted(covered - analyzed),
    }
    ok = not schema_errors and not multi and not report["uncovered_analyzed_pages"]

    outdir = root / "stage5"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "final.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"校验通过": ok, "schema错误": len(schema_errors),
            "多桶页": multi, "漏桶页": report["uncovered_analyzed_pages"]}
