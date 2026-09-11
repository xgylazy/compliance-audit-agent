# -*- coding: utf-8 -*-
"""精确追踪三条疑难注的去向：3.1注3 / 3.6注 / 3.2注3尾巴。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

NEEDLES = {
    "3.1的注3（例如，用户画像）": "例如，用户画像或特征标签",
    "3.6的注（肯定性动作）": "注：肯定性动作包括",
    "3.2注3的尾巴（用可能危害）": "用可能危害人身和财产安全",
}

print("== 在源 jsonl 中定位 ==")
for label, needle in NEEDLES.items():
    hits = []
    for line in open("input/GBT35273b.rows.jsonl", encoding="utf-8"):
        o = json.loads(line)
        if o.get("t") == "line" and needle in (o.get("text") or ""):
            hits.append((o["page"], round(o["y"], 1), o["text"][:40]))
    print(f"\n[{label}] 命中 {len(hits)} 行:")
    for pg, y, t in hits:
        print(f"  p{pg} y={y} | {t}")

print("\n== 源 jsonl 的 figure 记录核对 ==")
fig_with_text = 0
for line in open("input/GBT35273b.rows.jsonl", encoding="utf-8"):
    o = json.loads(line)
    if o.get("t") == "figure" and (o.get("text") or "").strip():
        fig_with_text += 1
        if any(nd in (o.get("text") or "") for nd in NEEDLES.values()):
            print(f"  p{o['page']} figure 含目标文本: {o['text'][:50]}")
print(f"  （含文本的 figure 记录共 {fig_with_text} 条）")

print("\n== v4 stage2 分片核对 ==")
p8 = json.loads(Path("workdir/stage2/page_analysis/page_8.json").read_text(encoding="utf-8"))
print("page_8 may_be_last:", repr((p8.get("may_be_last_page_semantics_text") or "")[:40]) or "None")
print("page_8 content_types:", p8["content_types"])
p7 = json.loads(Path("workdir/stage2/page_analysis/page_7.json").read_text(encoding="utf-8"))
for n in p7.get("sections") or []:
    if n["title"] == "3.2":
        print("page_7 3.2 text 尾30字:", repr(n["text"][-30:]))

print("\n== v4 最终输出核对 ==")
out = json.loads(Path("workdir/stage5/final.json").read_text(encoding="utf-8"))
for label, needle in NEEDLES.items():
    hits = []
    def w(ns, path):
        for nd in ns:
            k = " > ".join(path[-2:] + [nd["title"]])
            if needle in (nd.get("text") or ""):
                hits.append(k)
            w(nd.get("children", []), path + [nd["title"]])
    w(out["sections"], [])
    print(f"[{label}] 输出节: {hits or '无'}")

import json as _j
sp = _j.loads(Path("workdir/stage4/spreads.json").read_text(encoding="utf-8"))
print("\nspreads.json:", sp)
s3 = [s for s in out["sections"] if s["title"].startswith("3")][0]
for c in s3["children"]:
    if c["title"] in ("3.1", "3.2"):
        print(f"输出 {c['title']} pages={c['pages']} text长度={len(c['text'])}")
        if c["title"] == "3.1":
            print("  3.1 text 尾60字:", c["text"][-60:])
