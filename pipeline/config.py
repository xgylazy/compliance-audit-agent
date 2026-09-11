"""集中配置：LLM 连接参数与路径约定。

key 安全纪律：
  - 只从环境变量 / 项目根 .env 文件读取，绝不写进代码；
  - 真实环境变量优先于 .env（os.environ.setdefault）；
  - 兼容真实项目的 PDF2TREE_* 变量名（LLM_* 优先，PDF2TREE_* 兜底）。
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

INPUT_JSONL = PROJECT_ROOT / "input" / "GBT35273b.rows.jsonl"
GOLD_FINAL = PROJECT_ROOT / "final_output" / "final.json"      # 人工金标（只读，流水线绝不写这里）
PAGE_SCHEMA = PROJECT_ROOT / "schemas" / "page_analysis.schema.json"
FINAL_SCHEMA = PROJECT_ROOT / "schemas" / "final.schema.json"
WORKDIR = PROJECT_ROOT / "workdir"                             # 流水线产物根目录
DOTENV = PROJECT_ROOT / ".env"                                 # 本地密钥文件（不入库）


def _load_dotenv() -> None:
    """极简 .env 加载（KEY=VALUE，# 注释），不覆盖已存在的真实环境变量。"""
    if not DOTENV.exists():
        return
    for line in DOTENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            os.environ.setdefault(k, v)


_load_dotenv()


def _env(*names: str, default: str = "") -> str:
    """按优先级取第一个非空环境变量。"""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


# LLM 连接（OpenAI 兼容协议；provider 决定 base_url 默认值，见 llm_client）
LLM_PROVIDER = _env("LLM_PROVIDER", "PDF2TREE_LLM_PROVIDER", default="deepseek").strip().lower()
LLM_API_KEY = _env("LLM_API_KEY", "PDF2TREE_LLM_API_KEY")
LLM_BASE_URL = _env("LLM_BASE_URL", "PDF2TREE_LLM_BASE_URL")   # 留空则用 provider 默认
LLM_MODEL = _env("LLM_MODEL", "PDF2TREE_LLM_MODEL")            # 逗号分隔 = 多模型轮换；留空用 provider 默认
LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "PDF2TREE_LLM_TIMEOUT", default="120"))
LLM_TEMPERATURE = 0.0

GATE_MAX_RETRIES = 3   # 门控重试上限（带错误反馈）
PROMPT_VERSION = "v5"  # 参与 stage2 缓存 key：改 prompt 必须 bump，否则命中旧缓存
