# -*- coding: utf-8 -*-
"""逐条注审计：把金标里每一条"注"在 源jsonl → stage0 → LLM输出 三个层面逐一追踪，
区分"源数据缺失"与"LLM 遗漏"。

用法：python tests/audit_notes.py [--gold final_output/final.json] [--output workdir/stage5/final.json]
"""
import argparse
import json
import re
from pathlib import Path


def flatten_sections(nodes, path, out):
    for nd in nodes:
        p = path + [nd["title"]]
        out[" > ".join(p)] = nd.get("text", "")
        flatten_sections(nd.get("children", []), p, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="final_output/final.json")
    ap.add_argument("--output", default="workdir/stage5/final.json")
    ap.add_argument("--pages", default="workdir/stage0/pages")
    args = ap.parse_args()

    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    notes = []

    def walk(nodes, path):
        for nd in nodes:
            for ln in (nd.get("text") or "").split("\n"):
                if re.match(r"^注(\d|：|:)?", ln):
                    notes.append((" > ".join(path[-2:] + [nd["title"]]), ln))
            walk(nd.get("children", []), path + [nd["title"]])

    walk(gold["sections"], [])

    pages = {}
    for f in Path(args.pages).glob("page_*.json"):
        p = json.loads(f.read_text(encoding="utf-8"))
        pages[p["page"]] = "".join(ln["text"] for ln in p["lines"])

    out = json.loads(Path(args.output).read_text(encoding="utf-8"))
    out_texts = {}
    flatten_sections(out["sections"], [], out_texts)

    print(f"金标 sections 中共 {len(notes)} 条注：")
    miss_src, miss_llm = [], []
    for loc, note in notes:
        head = note[:18]
        src = [pg for pg, t in pages.items() if head in t]
        osec = [k for k, t in out_texts.items() if head in t]
        if not src:
            status, bucket = "源缺失", miss_src
        elif osec:
            status, bucket = "OK    ", None
        else:
            status, bucket = "LLM遗漏", miss_llm
        print(f"[{status}] {loc} | {note[:26]}… | 源页: {src or '无'} | 输出节: {osec or '无'}")
        if bucket is not None:
            bucket.append((loc, note))

    print(f"\n汇总：OK {len(notes) - len(miss_src) - len(miss_llm)} / "
          f"源缺失 {len(miss_src)} / LLM遗漏 {len(miss_llm)}")
    if miss_src:
        print("\n源缺失明细（LLM 无论如何无法转录，需修源数据或调整金标）：")
        for loc, note in miss_src:
            print(f"  - {loc} | {note[:40]}…")
    if miss_llm:
        print("\nLLM 遗漏明细（源里有但模型没转录，属 prompt/模型依从问题）：")
        for loc, note in miss_llm:
            print(f"  - {loc} | {note[:40]}…")


if __name__ == "__main__":
    main()
