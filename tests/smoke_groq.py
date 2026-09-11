# -*- coding: utf-8 -*-
"""Groq key 连通性冒烟测试：依次探测轮换链上的模型，验证鉴权与可用性。

用法：python tests/smoke_groq.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config, llm_client  # noqa: E402


def main():
    slots = llm_client._build_slots()
    print(f"provider={config.LLM_PROVIDER}  key={config.LLM_API_KEY[:8]}...{config.LLM_API_KEY[-4:]}")
    print(f"待探测模型 {len(slots)} 个\n")
    alive = []
    for s in slots:
        try:
            content, it, ot = s.call(
                [{"role": "user", "content": "只回复两个字：成功"}],
                response_format=None)
            text = llm_client.resp_text(content).replace("\n", " ")[:20]
            print(f"[可用] {s.name:<24} 回复: {text}  (in={it}, out={ot})")
            alive.append(s.name)
        except Exception as e:
            msg = str(e)
            reason = ("401/鉴权失败" if "401" in msg else
                      "404/模型不存在" if "404" in msg or "does not exist" in msg.lower() or "decommissioned" in msg.lower() else
                      "429/限流" if "429" in msg else
                      "其他错误")
            print(f"[不可用] {s.name:<24} {reason}: {msg[:120]}")
    print(f"\n可用模型 {len(alive)}/{len(slots)}: {', '.join(alive) if alive else '无'}")
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
