"""Цикл агента: LLM <-> инструменты (run_sql, lookup). Любой OpenAI-совместимый API."""
import json
import os
import time

from . import tools
from .prompts import build_system_prompt

MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
MAX_STEPS = 6
TOTAL_BUDGET = 70      # секунд на весь ответ (лимит конкурса — 90)
TOOL_JSON_LIMIT = 14000

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Выполнить ОДИН SQL-запрос SQLite (только SELECT/WITH) к базе реестра сделок и получить строки. "
                "Основное представление — deals_v. Для текстового поиска используй lower(col) LIKE '%...%'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "SQL-запрос"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": (
                "Найти в колонке реальные значения, похожие на текст (названия компаний, товаров, регионов, брокеров). "
                "Регистр и апострофы не важны. Вызывай ПЕРЕД SQL, когда в вопросе есть название."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "description": "deals_v, contracts, participants, brokers"},
                    "column": {"type": "string", "description": "например product_name, seller_name, region, name"},
                    "text": {"type": "string", "description": "что ищем, по-русски или как в данных"},
                },
                "required": ["table", "column", "text"],
            },
        },
    },
]

_use_temperature = True


def get_client():
    from openai import OpenAI

    return OpenAI(
        api_key=os.environ.get("LLM_API_KEY", ""),
        base_url=os.environ.get("LLM_BASE_URL") or None,
        timeout=40,
        max_retries=1,
    )


def _chat(client, messages, with_tools=True):
    """Один вызов LLM. Если модель не принимает temperature — повторяем без него."""
    global _use_temperature
    kwargs = {"model": MODEL, "messages": messages}
    if with_tools:
        kwargs["tools"] = TOOLS
    if _use_temperature:
        kwargs["temperature"] = 0
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        if _use_temperature and "temperature" in str(e).lower():
            _use_temperature = False
            kwargs.pop("temperature", None)
            return client.chat.completions.create(**kwargs)
        raise


def _clip(obj):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) > TOOL_JSON_LIMIT:
        s = s[:TOOL_JSON_LIMIT] + '..."}'  # LLM увидит обрезку
    return s


def _evidence_item(name, args, result):
    if name == "run_sql":
        return {"tool": "run_sql", "sql": args.get("query", ""), "columns": result.get("columns", []),
                "rows": result.get("rows", [])[:15], "row_count": result.get("row_count"),
                "truncated": result.get("truncated", False), "error": result.get("error"), "note": result.get("note")}
    return {"tool": "lookup", "args": args, "matches": result.get("matches", [])[:10],
            "error": result.get("error"), "note": result.get("note")}


def answer_question(question, history=None):
    t0 = time.time()
    messages = [{"role": "system", "content": build_system_prompt()}]
    for h in (history or [])[-6:]:
        role, content = h.get("role"), h.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content[:2000]})
    messages.append({"role": "user", "content": question})

    evidence = []
    try:
        client = get_client()
        for _ in range(MAX_STEPS):
            if time.time() - t0 > TOTAL_BUDGET:
                break
            resp = _chat(client, messages)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return {"answer": (msg.content or "").strip(), "evidence": evidence,
                        "seconds": round(time.time() - t0, 1)}
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = {}
                result = tools.dispatch(tc.function.name, args)
                evidence.append(_evidence_item(tc.function.name, args, result))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": _clip(result)})

        # Лимит шагов/времени: просим итог по уже полученному, без инструментов.
        messages.append({"role": "user", "content": (
            "Дай итоговый ответ только по уже полученным результатам. Если их недостаточно — прямо скажи, "
            "что в данных ответа не нашлось. Ничего не выдумывай.")})
        resp = _chat(client, messages, with_tools=False)
        return {"answer": (resp.choices[0].message.content or "").strip(), "evidence": evidence,
                "seconds": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {"answer": "Сервис временно недоступен, попробуйте ещё раз. / Xizmat vaqtincha ishlamayapti, qayta urinib ko'ring.",
                "error": f"{type(e).__name__}: {e}", "evidence": evidence, "seconds": round(time.time() - t0, 1)}
