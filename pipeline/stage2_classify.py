"""Stage 2 页面语义分类（LLM + 门控）。

两种运行方式：
  run()        真实 LLM：空白页由代码直接生成（不调 LLM）；其余页走门控调用；
               以"页内容 hash + PROMPT_VERSION"为缓存 key，失败落盘 failures/。
  run_from()   引导模式：用人工分片（信封格式）过三道门后作为 stage2 产物，
               无需 API key 即可离线跑通 stage3~5 全链路。
"""
import json
from pathlib import Path

from . import cache, config, gating, llm_client

SYSTEM_PROMPT = (
    "你是标准文档版面转录与分析器。这是【逐字转录】任务：text 字段必须逐字复制输入 body_lines 的原文，"
    "禁止改写、润色、同义替换、凭记忆补全——即使你认识这份标准，也必须以输入行为准"
    "（例如输入是'属于'就绝不能写成'应作为'）。"
    "在此基础上判断页面内容类型并抽取结构，输出严格符合给出的 JSON Schema（信封格式）。约定："
    "1) 空白页由系统判定，不会发给你；"
    "2) 正文延续页（本页无新标题、无特殊内容）输出空 content_types 且全部 payload 为 null；"
    "3) sections 只放本页新开始的节点，text 为本页页内文本（逐字）；承接上页的延续文本放 "
    "may_be_last_page_semantics_text（逐字）；"
    "4) 术语条目标题行只有编号时（如 3.1），title 只写编号，术语名行并入 text 首行；"
    "5) 页码只出现在 page 字段，payload 内不得重复记录页码；"
    "6) '前言'与'引言'是独立 section：标题行常印作'前　言'与'引　言'（全角空格），"
    "必须作为新 section 标题，title 规范化为'前言'/'引言'，引言内容严禁并入前言；"
    "7)【最高优先级】以'注：''注1：''注2：'开头的注释行及其换行续行，必须逐字完整保留在"
    "对应条目的 text 中，续行与首行直接拼接成一条完整注；每一条注都不能少、不能缩写、"
    "不能概括——漏一条注即判定为错误；"
    "8) 在保证逐字完整的前提下，JSON 输出尽量紧凑（无缩进无多余空白）；"
    "9) figure_text_lines 是从带框区域（figure 记录）提取的文字，同样逐字转录，但按内容归类："
    "是标题（如 目 次/前 言/引 言/附录X）→ 作为新 section 的标题（title 去全角空格规范化）；"
    "是注释或定义的续行（如某条注的后半段）→ 并入对应条目的 text（逐字）；"
    "是纯粹的图内标签 → 不写入正文。"
)


def _page_brief(p: dict) -> dict:
    body = [{"y": round(l["y"], 1), "size": l["size"],
             "cand": l["heading_candidate"], "score": l["heading_score"],
             "text": l["text"]}
            for l in p["lines"] if l["role"] == "body"]
    figs = [{"y": round(l["y"], 1), "cand": l["heading_candidate"],
             "text": l["text"]}
            for l in p["lines"] if l["role"] == "figure_text"]
    return {"page": p["page"], "line_count": p["line_count"], "is_blank": p["is_blank"],
            "header_footer_lines": [l["text"] for l in p["lines"] if l["role"] != "body" and l["role"] != "figure_text"],
            "figure_text_lines": figs,
            "body_lines": body}


def _neighbors(pages: dict, n: int, k: int = 5) -> list:
    out = []
    for m in (n - 1, n + 1):
        q = pages.get(m)
        if not q:
            continue
        cands = [l["text"] for l in q["lines"] if l.get("heading_candidate")][:k]
        out.append({"page": m, "heading_candidates": cands})
    return out


def _load_stage1(workdir: Path) -> dict:
    s1 = workdir / "stage1" / "pages"
    return {int(f.stem.split("_")[1]): json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(s1.glob("page_*.json"), key=lambda p: int(p.stem.split("_")[1]))}


def blank_record(n: int) -> dict:
    return {"page": n, "content_types": ["blank_page"], "fulltitle": None,
            "other_info": None, "table_of_contents": None,
            "may_be_last_page_semantics_text": None, "sections": None}


def run(pages=None, workdir=None, force=False) -> dict:
    root = Path(workdir or config.WORKDIR)
    out = root / "stage2" / "page_analysis"
    out.mkdir(parents=True, exist_ok=True)
    schema = gating.load_schema()
    all_pages = _load_stage1(root)
    targets = sorted(all_pages) if pages is None else sorted(pages)
    result = {}
    for n in targets:
        p = all_pages[n]
        dest = out / f"page_{n}.json"
        if p["is_blank"]:  # 空白页：代码直接生成，不经 LLM
            dest.write_text(json.dumps(blank_record(n), ensure_ascii=False, indent=2),
                            encoding="utf-8")
            result[n] = "blank(代码判定)"
            continue
        ck = cache.key_for("classify", config.PROMPT_VERSION, n,
                           [l["text"] for l in p["lines"]])
        cached = None if force else cache.get("classify", ck)
        if cached is not None:
            dest.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
            result[n] = "cache"
            continue
        user = (json.dumps({"schema": schema, "page": _page_brief(p),
                            "neighbors": _neighbors(all_pages, n)},
                           ensure_ascii=False)
                + "\n\n提醒：text 逐字转录输入原文，禁止改写；所有'注'必须完整保留，一条不能少。")
        data, errors, raw, ok = None, [], "", False
        try:
            for attempt in range(1, config.GATE_MAX_RETRIES + 1):
                msg = user
                if errors:
                    msg = user + "\n\n上次输出未通过校验，错误如下，请修正后重新输出完整 JSON：\n" \
                          + "\n".join(errors)
                raw = llm_client.chat_json(SYSTEM_PROMPT, msg, json_schema_obj=schema)
                data, errors = gating.gated_llm_output(raw, schema, n)
                if data is not None:
                    cache.put("classify", ck, data)
                    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
                    result[n] = f"llm(第{attempt}次通过)"
                    ok = True
                    break
        except Exception as e:
            # 所有模型槽位耗尽（鉴权失败/限流打满/网络异常）：本页失败落盘，
            # 不中断流水线，继续跑其他页
            errors = [f"LLM 调用失败（所有模型槽位耗尽）: {e}"]
        if not ok:
            faildir = root / "stage2" / "failures"
            faildir.mkdir(parents=True, exist_ok=True)
            (faildir / f"page_{n}.json").write_text(
                json.dumps({"page": n, "errors": errors, "last_raw": raw},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            result[n] = "FAILED(见 stage2/failures/)"
    return result


def run_from(frag_dir, pages=None, workdir=None) -> dict:
    """引导模式：人工分片过三道门 → stage2 产物（离线，不调 LLM）。"""
    root = Path(workdir or config.WORKDIR)
    src = Path(frag_dir)
    out = root / "stage2" / "page_analysis"
    out.mkdir(parents=True, exist_ok=True)
    schema = gating.load_schema()
    result = {}
    for f in sorted(src.glob("page_*.json"), key=lambda p: int(p.stem.split("_")[1])):
        n = int(f.stem.split("_")[1])
        if pages is not None and n not in pages:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        raw = json.dumps(data, ensure_ascii=False)
        d1, errs = gating.door1_parse(raw)
        if d1 is None:
            result[n] = f"INVALID {errs}"
            continue
        errs = gating.door2_schema(d1, schema) + gating.door3_envelope(d1, n)
        if errs:
            result[n] = f"INVALID {errs}"
            continue
        (out / f"page_{n}.json").write_text(
            json.dumps(d1, ensure_ascii=False, indent=2), encoding="utf-8")
        result[n] = "from-fragments(三门通过)"
    return result
