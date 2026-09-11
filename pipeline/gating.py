"""门控：LLM 原始输出 → 三道门 → 合法信封。

门1 格式门：输出必须可解析为 JSON（容忍 markdown 代码围栏）。
门2 schema 门：jsonschema 可用则用 schemas/page_analysis.schema.json 校验；
    未安装 jsonschema 时降级为内置迷你校验器（覆盖同一份 schema 的结构约束）。
门3 业务门：信封一致性规则——page 与实际页码一致、content_types 与 payload
    非空情况严格对应、toc/section 节点结构完整。

三门全过才允许落盘；失败信息回灌 prompt 重试（见 stage2）。
"""
import json
from pathlib import Path

from . import config

try:
    import jsonschema
    HAVE_JSONSCHEMA = True
except ImportError:
    HAVE_JSONSCHEMA = False

PAYLOAD_KEYS = ["fulltitle", "other_info", "table_of_contents",
                "may_be_last_page_semantics_text", "sections"]
CONTENT_TYPES = ("fulltitle", "table_of_contents", "blank_page", "sections")


def load_schema() -> dict:
    return json.loads(Path(config.PAGE_SCHEMA).read_text(encoding="utf-8"))


def door1_parse(raw: str):
    """门1：格式门。返回 (data | None, errors)。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as e:
        return None, [f"门1失败: 输出不是合法 JSON（{e}）"]


def _resolve_ref(schema, root):
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 10:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
        seen += 1
    return schema


def _walk_schema(data, schema, root, path, errs):
    """通用迷你 JSON Schema 走查器，覆盖本项目 schema 用到的子集：
    $ref / type / required / properties / additionalProperties / items /
    enum / minimum / uniqueItems / minItems。"""
    schema = _resolve_ref(schema, root)
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        py = {"object": dict, "array": list, "string": str, "integer": int,
              "number": (int, float), "boolean": bool, "null": type(None)}
        ok = any(isinstance(data, py[x])
                 and not (x in ("integer", "number") and isinstance(data, bool))
                 for x in types)
        if not ok:
            errs.append(f"门2失败: {path or '根'} 类型应为 {t}，实际 {type(data).__name__}")
            return
    if "enum" in schema and data not in schema["enum"]:
        errs.append(f"门2失败: {path or '根'} 取值 {data!r} 不在枚举 {schema['enum']}")
    if isinstance(data, dict):
        for r in schema.get("required", []):
            if r not in data:
                errs.append(f"门2失败: {path or '根'} 缺必填字段 {r}")
        props = schema.get("properties", {})
        for k, v in data.items():
            if k in props:
                _walk_schema(v, props[k], root, f"{path}/{k}", errs)
            elif schema.get("additionalProperties") is False:
                errs.append(f"门2失败: {path or '根'} 出现 schema 外字段 {k}")
    elif isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            errs.append(f"门2失败: {path or '根'} 至少需要 {schema['minItems']} 项")
        if schema.get("uniqueItems"):
            keys = [json.dumps(x, ensure_ascii=False, sort_keys=True) for x in data]
            if len(keys) != len(set(keys)):
                errs.append(f"门2失败: {path or '根'} 存在重复项")
        if "items" in schema:
            for i, v in enumerate(data):
                _walk_schema(v, schema["items"], root, f"{path}[{i}]", errs)
    if "minimum" in schema and isinstance(data, (int, float)) \
            and not isinstance(data, bool) and data < schema["minimum"]:
        errs.append(f"门2失败: {path or '根'} 小于最小值 {schema['minimum']}")


def door2_schema(data, schema: dict) -> list:
    """门2：schema 门（jsonschema 可用则用之，否则用通用迷你走查器）。"""
    if HAVE_JSONSCHEMA:
        try:
            jsonschema.validate(data, schema)
            return []
        except jsonschema.ValidationError as e:
            path = "/".join(str(x) for x in e.absolute_path) or "根"
            return [f"门2失败: {e.message}（路径: {path}）"]
    errs = []
    _walk_schema(data, schema, schema, "", errs)
    return errs


def door3_envelope(data: dict, page_no: int) -> list:
    """门3：信封业务一致性规则。"""
    errs = []
    if data.get("page") != page_no:
        errs.append(f"门3失败: page={data.get('page')} 与实际页码 {page_no} 不一致")
    ct = data.get("content_types", [])
    vals = {k: data.get(k) for k in PAYLOAD_KEYS}
    nonnull = [k for k, v in vals.items() if v is not None]

    if "blank_page" in ct:
        if nonnull:
            errs.append(f"门3失败: blank_page 页不得有非空 payload（{nonnull}）")
        if len(ct) != 1:
            errs.append("门3失败: blank_page 不得与其他内容类型并存")
        return errs

    if "fulltitle" in ct:
        if vals["fulltitle"] is None and vals["other_info"] is None:
            errs.append("门3失败: content_types 含 fulltitle 但 fulltitle/other_info 均为 null")
    elif vals["fulltitle"] is not None or vals["other_info"] is not None:
        errs.append("门3失败: fulltitle/other_info 非 null 但 content_types 不含 fulltitle")

    if ("table_of_contents" in ct) != (vals["table_of_contents"] is not None):
        errs.append("门3失败: table_of_contents payload 与 content_types 不对应")

    if ("sections" in ct) != (vals["sections"] is not None
                              or vals["may_be_last_page_semantics_text"] is not None):
        errs.append("门3失败: sections/may_be_last_page_semantics_text 与 content_types 不对应")

    if not ct and nonnull:
        errs.append(f"门3失败: 正文延续页（空 content_types）不得有 payload（{nonnull}）")

    def _toc(nodes, path):
        for i, n in enumerate(nodes or []):
            if not isinstance(n, dict) or "title" not in n or "children" not in n:
                errs.append(f"门3失败: toc 节点 {path}[{i}] 缺 title/children")
            else:
                _toc(n.get("children"), f"{path}[{i}].children")

    def _sec(nodes, path):
        for i, n in enumerate(nodes or []):
            if not isinstance(n, dict) or "title" not in n or not n.get("pages"):
                errs.append(f"门3失败: section 节点 {path}[{i}] 缺 title/pages")
            else:
                _sec(n.get("children"), f"{path}[{i}].children")

    if vals["table_of_contents"] is not None:
        _toc(vals["table_of_contents"].get("items"), "items")
    if vals["sections"] is not None:
        _sec(vals["sections"], "sections")
    return errs


def gated_llm_output(raw: str, schema: dict, page_no: int):
    """依次过三道门。返回 (data | None, errors)。"""
    data, errs = door1_parse(raw)
    if errs:
        return None, errs
    errs = door2_schema(data, schema)
    if errs:
        return None, errs
    errs = door3_envelope(data, page_no)
    if errs:
        return None, errs
    return data, []
