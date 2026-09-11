"""统一 LLM 客户端工厂：deepseek / openai / nvidia / groq → 同一 chat_json 接口。

改编自真实项目验证过的客户端工厂，保留其实战行为，底座换用 openai SDK
（OpenAI 兼容协议，避免引入 LangChain 依赖；NVIDIA NIM 同样暴露兼容端点）：

  - provider 分派：none / deepseek / openai / nvidia / groq
  - 多模型轮换：LLM_MODEL 支持逗号分隔，当前模型 429 打满自动切下一个
  - 双层重试：模型内 429/413 退避重试 + 门控层错误回灌重试（分钟级 TPM 窗口，退避给足）
  - Groq 实战约束：免费层 8K TPM 把 max_completion_tokens 计入请求成本
    （413 = input + max_completion_tokens），故用 2048；qwen3* 系必须
    reasoning_effort=none（思考内联 content 会截断 JSON），gpt-oss 用 low
    （不接受 none，low 档思考放独立字段不占 content）
  - 推理模型输出上限 8192（思考占输出 token，默认上限太小 → content 为空）
  - 占位符 key 直接拒绝（.env 里填假 Key 时不发无效请求）
  - <think> 块剥离 + markdown 围栏清理（resp_text 统一入口）
  - 本地用量统计（Langfuse 的零依赖替代）：模型/成败/耗时/输入输出 token，
    run_pipeline 结束时打汇总表；LLM_USAGE_LOG=0 可关闭
"""
import json
import os
import re
import threading
import time

from . import config

PROVIDERS = ("none", "deepseek", "openai", "nvidia", "groq")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
NVIDIA_DEFAULT_MODEL = "minimaxai/minimax-m3"
# Groq 的 TPM 配额把 max_completion_tokens 也计入请求成本（413 提示 Requested
# = input + max_completion_tokens），免费层 8K TPM 下只能用官方示例的 2048
GROQ_MAX_COMPLETION_TOKENS = 2048
GROQ_DEFAULT_MODELS = "qwen/qwen3.6-27b,openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b"
DEFAULT_MODEL_BY_PROVIDER = {"deepseek": "deepseek-chat", "openai": "gpt-4o-mini"}

# 占位符 token 识别：.env 里先填个假 Key 时直接拒绝，避免拿假 Key 打无效请求
_PLACEHOLDER_RE = re.compile(
    r"placeholder|replace[_-]?(?:with|me)|your[_-]?(?:api[_-]?)?(?:key|token)|dummy|fake|changeme|xxx+|todo",
    re.IGNORECASE,
)

# 推理模型（Groq qwen3 系）把思考过程内联在 content 里（<think>...</think>），
# JSON 在标签之后；GPT-OSS 系则放在单独字段。统一剥掉 think 块再解析。
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# ---- 本地用量统计（零依赖，线程安全）----
_USAGE_ENABLED = os.getenv("LLM_USAGE_LOG", os.getenv("PDF2TREE_LLM_USAGE_LOG", "1")) \
    .strip().lower() not in ("0", "false", "no", "off")
_USAGE_LOCK = threading.Lock()
_USAGE_LOG: list = []

_SLOTS = None       # 懒构建的模型轮换槽位
_KEY_SOURCE = ""    # 实际生效的 key 来源（诊断用）
_OPENAI_CLIS = {}   # base_url -> OpenAI 客户端（同 provider 共享）


def resp_text(raw: str) -> str:
    """剥掉 <think> 块与 markdown 围栏后的纯文本（门1 解析前的统一入口）。"""
    raw = _THINK_RE.sub("", raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
    return raw


def _usage_record(model: str, ok: bool, dt: float, *, in_tok=0, out_tok=0, err=None):
    if not _USAGE_ENABLED:
        return
    rec = {"ts": time.strftime("%H:%M:%S"), "model": model, "ok": ok,
           "dt": round(dt, 2), "in_tok": int(in_tok or 0), "out_tok": int(out_tok or 0),
           "err": str(err or "")[:100]}
    with _USAGE_LOCK:
        _USAGE_LOG.append(rec)


def usage_summary() -> list:
    """按模型聚合：[(model, calls, ok, in_tok, out_tok, total_s, fail_note), ...]。"""
    with _USAGE_LOCK:
        rows = list(_USAGE_LOG)
    agg: dict = {}
    for r in rows:
        a = agg.setdefault(r["model"], {"calls": 0, "ok": 0, "in_tok": 0,
                                        "out_tok": 0, "dt": 0.0, "errs": {}})
        a["calls"] += 1
        a["ok"] += 1 if r["ok"] else 0
        a["in_tok"] += r["in_tok"]
        a["out_tok"] += r["out_tok"]
        a["dt"] += r["dt"]
        if not r["ok"]:
            key = r["err"].split("|")[0][:40] or "error"
            a["errs"][key] = a["errs"].get(key, 0) + 1
    return [(m, v["calls"], v["ok"], v["in_tok"], v["out_tok"], round(v["dt"], 1),
             "; ".join(f"{k}×{n}" for k, n in v["errs"].items()))
            for m, v in agg.items()]


def _client_for(base_url: str):
    """同 base_url 共享一个 OpenAI 客户端（懒导入：纯代码阶段无需安装 openai）。"""
    if base_url not in _OPENAI_CLIS:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("未安装 openai：pip install openai") from e
        _OPENAI_CLIS[base_url] = OpenAI(api_key=config.LLM_API_KEY,
                                        base_url=base_url,
                                        timeout=config.LLM_TIMEOUT)
    return _OPENAI_CLIS[base_url]


class _ModelSlot:
    """一个轮换槽位 = 模型名 + 该模型的请求默认参数（Groq/推理模型差异在此吸收）。"""

    def __init__(self, name: str, base_url: str, defaults: dict):
        self.name = name
        self.base_url = base_url
        self.defaults = defaults

    def call(self, messages: list, response_format=None) -> tuple:
        """单次调用。返回 (content, in_tok, out_tok)；非 2xx 直接抛异常交给轮换层。"""
        client = _client_for(self.base_url)
        kwargs = dict(self.defaults)
        kwargs["messages"] = messages
        if response_format is not None:
            kwargs["response_format"] = response_format
        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        usage = getattr(resp, "usage", None)
        it = getattr(usage, "prompt_tokens", 0) if usage else 0
        ot = getattr(usage, "completion_tokens", 0) if usage else 0
        content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        return content, it, ot


def _build_slots() -> list:
    """按 config 解析 provider/key/model → 轮换槽位列表（含各家实战约束）。"""
    provider = config.LLM_PROVIDER
    if provider not in PROVIDERS:
        raise ValueError(f"未知 LLM_PROVIDER: {provider}（可选 {'/'.join(PROVIDERS)}）")
    if provider == "none":
        raise ValueError("LLM_PROVIDER=none，未启用任何 LLM")
    key = (config.LLM_API_KEY or "").strip()
    key_source = "LLM_API_KEY（.env/环境变量）"
    if not key:
        # 官方 SDK 约定：Groq() 无参构造时读 GROQ_API_KEY 环境变量；
        # 这里对齐同一约定——.env 没写 key 时回退各家官方环境变量名
        fallback_name = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
                         "nvidia": "NVIDIA_API_KEY", "deepseek": "DEEPSEEK_API_KEY"
                         }.get(provider)
        if fallback_name:
            key = (os.environ.get(fallback_name) or "").strip()
            key_source = fallback_name + "（环境变量）"
    global _KEY_SOURCE
    _KEY_SOURCE = key_source
    if not key:
        raise ValueError("未配置 LLM_API_KEY（.env），且官方环境变量（如 GROQ_API_KEY）也未设置")
    if _PLACEHOLDER_RE.search(key):
        raise ValueError("API key 是占位符，LLM 功能跳过；买到真实 token 后替换 .env 即可")

    if provider == "nvidia":
        base_url = config.LLM_BASE_URL or NVIDIA_BASE_URL
        models = [m.strip() for m in config.LLM_MODEL.split(",") if m.strip()]
        if not models:
            models = [NVIDIA_DEFAULT_MODEL]
    else:
        base_url = config.LLM_BASE_URL or (
            DEEPSEEK_BASE_URL if provider == "deepseek"
            else GROQ_BASE_URL if provider == "groq" else "")
        models = [m.strip() for m in config.LLM_MODEL.split(",") if m.strip()]
        if not models and provider == "groq":
            models = [m.strip() for m in GROQ_DEFAULT_MODELS.split(",")]
        if not models:
            models = [DEFAULT_MODEL_BY_PROVIDER.get(provider, "")]
        if not models or not models[0]:
            raise ValueError(f"{provider} 必须配置 LLM_MODEL")

    slots = []
    for m in models:
        defaults = {"model": m, "temperature": config.LLM_TEMPERATURE, "top_p": 0.95}
        if provider == "groq":
            # TPM 把输出预算计入成本：8192 会直接撞 8K TPM（413），用官方示例值 2048
            defaults["max_completion_tokens"] = GROQ_MAX_COMPLETION_TOKENS
        else:
            # 推理模型思考占输出 token，默认上限太小会导致思考未结束、content 为空
            defaults["max_completion_tokens"] = 8192
        body = {}
        if "qwen3" in m.lower():
            # Groq qwen3 系：思考过程默认内联在 content 且计入输出预算，
            # 2048 cap 下 JSON 必被截断；分类任务是纯 JSON 任务，不需要思考
            body["reasoning_effort"] = "none"
        elif "gpt-oss" in m.lower():
            # gpt-oss 不接受 "none"（400：must be one of low/medium/high），
            # 用最低档 "low"：思考放独立字段，不占 content
            body["reasoning_effort"] = "low"
        if body:
            defaults["extra_body"] = body
        slots.append(_ModelSlot(m, base_url, defaults))
    return slots


def _get_slots() -> list:
    global _SLOTS
    if _SLOTS is None:
        _SLOTS = _build_slots()
    return _SLOTS


_PER_MODEL_TRIES = 2   # 每个模型 429/413 退避重试次数
_WAIT_S = 20           # TPM/RPM 窗口分钟级，退避给足


def chat_json(system: str, user: str, json_schema_obj=None) -> str:
    """统一入口：多模型轮换 + 结构化输出级联 + think 剥离。

    结构化输出级联（同一模型内降级，不烧轮换次数）：
      json_schema → json_object → 纯文本（端点不支持时 400 降级）。
    429/413：模型内退避重试 _PER_MODEL_TRIES 次，仍限流换下一个模型；
    全部槽位耗尽后抛最后一次异常（调用方走门控的失败落盘路径）。
    """
    slots = _get_slots()
    formats = []
    if json_schema_obj is not None:
        formats.append({"type": "json_schema",
                        "json_schema": {"name": "page_analysis", "strict": False,
                                        "schema": json_schema_obj}})
    formats.append({"type": "json_object"})
    formats.append(None)

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    last_err = None
    for slot in slots:
        fmt_idx, tries = 0, 0
        while fmt_idx < len(formats) and tries < _PER_MODEL_TRIES + len(formats):
            t0 = time.perf_counter()
            try:
                content, it, ot = slot.call(messages, formats[fmt_idx])
                _usage_record(slot.name, True, time.perf_counter() - t0,
                              in_tok=it, out_tok=ot)
                return resp_text(content)
            except Exception as e:
                _usage_record(slot.name, False, time.perf_counter() - t0, err=e)
                last_err = RuntimeError(f"[{slot.name}] {e}")
                es = str(e)
                if ("429" in es or "413" in es) and tries < _PER_MODEL_TRIES - 1:
                    tries += 1
                    time.sleep(_WAIT_S)   # 分钟级限流窗口，退避给足
                    continue
                if ("400" in es or "response_format" in es.lower()) \
                        and formats[fmt_idx] is not None:
                    fmt_idx += 1          # 端点不支持该格式 → 同模型降级
                    continue
                break                     # 其他错误：换下一个模型
    raise last_err or RuntimeError("LLM 调用失败：无可用模型槽位")
