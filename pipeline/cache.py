"""按页缓存：key = sha256(页内容 + prompt 版本 + 页码)。
改 prompt 只需 bump config.PROMPT_VERSION，即自动全量重跑；
未 bump 时改动过的页（内容 hash 变化）自动重跑，其余命中缓存。
"""
import hashlib
import json
from pathlib import Path

from . import config


def key_for(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:24]


def get(stage: str, key: str):
    p = Path(config.WORKDIR) / "cache" / stage / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def put(stage: str, key: str, data) -> None:
    p = Path(config.WORKDIR) / "cache" / stage / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
