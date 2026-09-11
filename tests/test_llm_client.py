# -*- coding: utf-8 -*-
"""llm_client 离线测试：不需要 API key，不发起网络请求。

用法：python tests/test_llm_client.py
覆盖：占位符 key 拒绝 / 缺 key / provider=none / think 剥离 /
      用量聚合 / groq 默认轮换链与实战约束 / deepseek 默认模型 / 门控三道门。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config, llm_client  # noqa: E402


def test_placeholder_key_rejected():
    config.LLM_PROVIDER = "groq"
    config.LLM_API_KEY = "sk-your-api-key-here"
    llm_client._SLOTS = None
    try:
        llm_client._build_slots()
        raise AssertionError("占位符 key 未被拒绝")
    except ValueError as e:
        assert "占位符" in str(e), e
    print("OK  占位符 key 拒绝")


def test_missing_key_rejected():
    config.LLM_API_KEY = ""
    llm_client._SLOTS = None
    try:
        llm_client._build_slots()
        raise AssertionError("缺 key 未报错")
    except ValueError as e:
        assert "LLM_API_KEY" in str(e), e
    print("OK  缺 key 拒绝")


def test_provider_none_rejected():
    config.LLM_PROVIDER = "none"
    llm_client._SLOTS = None
    try:
        llm_client._build_slots()
        raise AssertionError("provider=none 未报错")
    except ValueError as e:
        assert "none" in str(e), e
    print("OK  provider=none 拒绝")


def test_resp_text_strips_think_and_fence():
    raw = "<think>推理过程，不该出现在JSON里</think>\n```json\n{\"a\": 1}\n```"
    assert llm_client.resp_text(raw) == '{"a": 1}', repr(llm_client.resp_text(raw))
    raw2 = "```json\n{\"b\": [1, 2]}\n```"
    assert llm_client.resp_text(raw2) == '{"b": [1, 2]}'
    raw3 = '{"c": "无围栏"}'
    assert llm_client.resp_text(raw3) == raw3
    print("OK  resp_text 剥离 think 块与围栏")


def test_usage_summary():
    llm_client._USAGE_LOG.clear()
    llm_client._usage_record("m1", True, 0.5, in_tok=10, out_tok=5)
    llm_client._usage_record("m1", False, 0.1, err="[m1] Error code: 429 rate limit")
    rows = llm_client.usage_summary()
    assert rows[0][0] == "m1" and rows[0][1] == 2 and rows[0][2] == 1
    assert rows[0][3] == 10 and rows[0][4] == 5
    assert "429" in rows[0][6]
    print("OK  用量统计聚合")


def test_groq_slot_defaults():
    config.LLM_PROVIDER = "groq"
    config.LLM_API_KEY = "gsk_slot_test_key_0000000001"
    config.LLM_MODEL = ""  # 触发默认轮换链
    llm_client._SLOTS = None
    slots = llm_client._build_slots()
    assert len(slots) == 4
    assert slots[0].base_url == llm_client.GROQ_BASE_URL
    assert slots[0].defaults["max_completion_tokens"] == 2048  # TPM 预算约束
    assert slots[0].defaults["extra_body"] == {"reasoning_effort": "none"}   # qwen3
    assert slots[1].defaults["extra_body"] == {"reasoning_effort": "low"}    # gpt-oss
    print("OK  groq 默认轮换链 + 实战约束（2048 cap / reasoning_effort）")


def test_deepseek_defaults():
    config.LLM_PROVIDER = "deepseek"
    config.LLM_MODEL = ""
    llm_client._SLOTS = None
    slots = llm_client._build_slots()
    assert slots[0].name == "deepseek-chat"
    assert slots[0].base_url == llm_client.DEEPSEEK_BASE_URL
    assert slots[0].defaults["max_completion_tokens"] == 8192
    print("OK  deepseek 默认模型与 base_url")


def test_gate_rejects_bad_envelope():
    from pipeline import gating
    schema = gating.load_schema()
    # 缺 content_types → 门2
    data, errs = gating.gated_llm_output('{"page": 3}', schema, 3)
    assert data is None and errs
    # page 与页码不一致 → 门3
    raw = ('{"page": 9, "content_types": ["blank_page"], "fulltitle": null,'
           '"other_info": null, "table_of_contents": null,'
           '"may_be_last_page_semantics_text": null, "sections": null}')
    data, errs = gating.gated_llm_output(raw, schema, 3)
    assert data is None and any("页码" in e for e in errs)
    # 合法信封 → 通过
    ok = ('{"page": 2, "content_types": ["blank_page"], "fulltitle": null,'
          '"other_info": null, "table_of_contents": null,'
          '"may_be_last_page_semantics_text": null, "sections": null}')
    data, errs = gating.gated_llm_output(ok, schema, 2)
    assert data is not None and not errs
    print("OK  门控三道门拒绝/放行")


if __name__ == "__main__":
    test_placeholder_key_rejected()
    test_missing_key_rejected()
    test_provider_none_rejected()
    test_resp_text_strips_think_and_fence()
    test_usage_summary()
    test_groq_slot_defaults()
    test_deepseek_defaults()
    test_gate_rejects_bad_envelope()
    print("\n全部离线测试通过 ✓")
