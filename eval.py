"""eval.py — 金标判卷器：workdir/stage5/final.json vs final_output/final.json。

结构层：TOC 条目序列、toc.page、blank_pages、fulltitle、
        sections 标题路径（缺失/多余 = WARN，缺失或不一致 = FAIL）。
文本层：按标题路径配对，比 text 相似度（difflib）与 pages 集合。
报告写入 workdir/eval/eval_<时间戳>.md，控制台打印摘要。

用法：python eval.py [--output workdir/stage5/final.json] [--gold final_output/final.json]
"""
import argparse
import difflib
import json
import time
from pathlib import Path


def flatten_toc(items, out):
    for it in items:
        out.append(it["title"])
        flatten_toc(it.get("children", []), out)


def flatten_sections(nodes, path, out):
    for nd in nodes:
        p = path + [nd["title"]]
        out[" > ".join(p)] = {"pages": list(nd.get("pages", [])),
                              "text": nd.get("text", "")}
        flatten_sections(nd.get("children", []), p, out)


def main():
    ap = argparse.ArgumentParser(description="金标判卷器")
    ap.add_argument("--output", default="workdir/stage5/final.json")
    ap.add_argument("--gold", default="final_output/final.json")
    args = ap.parse_args()

    out = json.loads(Path(args.output).read_text(encoding="utf-8"))
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))

    checks, text_rows = [], []

    # fulltitle
    checks.append(("fulltitle", "", out["fulltitle"] == gold["fulltitle"]))

    # TOC 条目序列
    ot, gt = [], []
    flatten_toc(out["table_of_contents"]["items"], ot)
    flatten_toc(gold["table_of_contents"]["items"], gt)
    pair_ok = sum(1 for a, b in zip(ot, gt) if a == b)
    checks.append(("TOC 条目序列", f"逐位匹配 {pair_ok}/{max(len(ot), len(gt))}"
                   f"（out {len(ot)} 条 / gold {len(gt)} 条）", ot == gt))

    # toc.page / blank_pages
    checks.append(("toc.page", f"out={out['table_of_contents']['page']} "
                   f"gold={gold['table_of_contents']['page']}",
                   out["table_of_contents"]["page"] == gold["table_of_contents"]["page"]))
    checks.append(("blank_pages", f"out={out['blank_pages']} gold={gold['blank_pages']}",
                   out["blank_pages"] == gold["blank_pages"]))

    # sections：缺失（FAIL）/ 多余（WARN）/ 文本与页码（FAIL）
    os_, gs = {}, {}
    flatten_sections(out["sections"], [], os_)
    flatten_sections(gold["sections"], [], gs)
    missing = [p for p in gs if p not in os_]
    extra = [p for p in os_ if p not in gs]
    checks.append(("sections 缺失（金标有输出无）", str(missing) if missing else "0",
                   not missing))
    checks.append(("sections 多余（输出有金标无，常为金标未标注区）",
                   str(extra) if extra else "0", not extra))
    for p in sorted(set(gs) & set(os_)):
        ratio = difflib.SequenceMatcher(None, os_[p]["text"], gs[p]["text"]).ratio()
        pages_ok = os_[p]["pages"] == gs[p]["pages"]
        text_rows.append((p, ratio, pages_ok))
    mism = [(p, r, pk) for p, r, pk in text_rows if r < 0.999 or not pk]
    checks.append(("sections 文本/页码不一致",
                   "; ".join(f"{p}(相似度{r:.4f},pages={'✓' if pk else '✗'})"
                             for p, r, pk in mism) or "0", not mism))

    ok = all(c[2] for c in checks)
    warns = [c[0] for c in checks if not c[2] and c[0].startswith("sections 多余")]

    ts = time.strftime("%Y%m%d_%H%M%S")
    rep_dir = Path("workdir/eval")
    rep_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# 评测报告 {ts}", f"- 输出: {args.output}", f"- 金标: {args.gold}", ""]
    for name, detail, passed in checks:
        tag = "PASS" if passed else ("WARN" if name in warns else "FAIL")
        lines.append(f"- [{tag}] {name} {detail}")
    lines += ["", "## sections 文本相似度明细"]
    lines += [f"- {p}: 相似度 {r:.4f}，pages {'✓' if pk else '✗'}"
              for p, r, pk in text_rows]
    (rep_dir / f"eval_{ts}.md").write_text("\n".join(lines), encoding="utf-8")

    for name, detail, passed in checks:
        tag = "PASS" if passed else ("WARN" if name in warns else "FAIL")
        print(f"[{tag}] {name} {detail}")
    print(f"\n结论: {'PASS' if ok else ('PASS(WARN)' if warns else 'FAIL')}"
          f"（报告: {rep_dir / f'eval_{ts}.md'}）")


if __name__ == "__main__":
    main()
