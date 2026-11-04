"""Цикл агента: LLM <-> инструменты (run_sql, lookup). Любой OpenAI-совместимый API."""
import json
import logging
import time

from . import tools
from .conversation import needs_history, relevant_history
from .llm import create_client, describe_error, get_config, next_config
from .presentation import format_answer
from .prompts import build_system_prompt

logger = logging.getLogger(__name__)
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
    config = next_config()
    logger.info("LLM question: using %s (value hidden)", config.key_name)
    return create_client(config=config)


def _chat(client, messages, with_tools=True, deadline=None):
    """Один вызов LLM. Если модель не принимает temperature — повторяем без него."""
    global _use_temperature
    kwargs = {"model": get_config().model, "messages": messages}
    if with_tools:
        kwargs["tools"] = TOOLS
    if _use_temperature:
        kwargs["temperature"] = 0

    def send():
        remaining = deadline - time.monotonic() if deadline is not None else 30
        if remaining <= 0:
            raise TimeoutError("Исчерпан бюджет времени на ответ.")
        return client.with_options(timeout=min(30, remaining)).chat.completions.create(**kwargs)

    try:
        return send()
    except Exception as e:  # noqa: BLE001
        if (_use_temperature and getattr(e, "status_code", None) == 400
                and "temperature" in str(e).lower()):
            _use_temperature = False
            kwargs.pop("temperature", None)
            return send()
        raise


def _clip(obj):
    out = dict(obj)
    for key in ("rows", "matches"):
        if key in out:
            out[key] = list(out[key])
    while len(json.dumps(out, ensure_ascii=False, default=str)) > TOOL_JSON_LIMIT:
        key = next((k for k in ("rows", "matches") if out.get(k)), None)
        if key is None:
            return json.dumps({"error": "Результат слишком большой. Уточни запрос или выбери меньше колонок."}, ensure_ascii=False)
        out[key].pop()
        out["truncated"] = True
        if key == "rows":
            out["row_count"] = len(out[key])
        out["note"] = "Результат сокращён по размеру. Для точных итогов выполни агрегатный SQL-запрос."
    return json.dumps(out, ensure_ascii=False, default=str)


def _evidence_item(name, args, result):
    if name == "run_sql":
        return {"tool": "run_sql", "sql": result.get("executed_sql", args.get("query", "")), "columns": result.get("columns", []),
                "rows": result.get("rows", []), "row_count": result.get("row_count"),
                "truncated": result.get("truncated", False), "error": result.get("error"), "note": result.get("note")}
    return {"tool": "lookup", "args": args, "matches": result.get("matches", []),
            "error": result.get("error"), "note": result.get("note")}


def answer_question(question, history=None):
    t0 = time.monotonic()
    deadline = t0 + TOTAL_BUDGET
    messages = [{"role": "system", "content": build_system_prompt()}]
    context = relevant_history(question, history)
    if needs_history(question) and not context:
        return {"answer": "Уточните, о каких сделках идёт речь: какие год, тип или другие условия нужно сохранить? / Qaysi bitimlar nazarda tutilganini aniqlashtiring: yil, tur yoki boshqa shartlar.",
                "evidence": [], "seconds": round(time.monotonic() - t0, 1), "needs_clarification": True}
    for h in context:
        role, content = h.get("role"), h.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content[:2000]})
    current = question
    if context:
        current += "\n\nЭто продолжение последнего самостоятельного вопроса. Сохрани его условия (год, тип, товар, регион), если я их явно не изменил. Условия бери из вопросов, прошлые числа не считай доказательством. Текущий ответ заново проверь по базе."
    messages.append({"role": "user", "content": current})

    evidence = []
    client = None
    try:
        client = get_client()
        for _ in range(MAX_STEPS):
            if time.monotonic() >= deadline:
                break
            resp = _chat(client, messages, deadline=deadline)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                if not any(not item.get("error") for item in evidence):
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content": (
                        "Ответ ещё не проверен по базе. Сначала используй run_sql или lookup. "
                        "Если инструменты не работают, сообщи о невозможности проверки, а не об отсутствии данных.")})
                    continue
                return {"answer": format_answer(msg.content or "", evidence), "evidence": evidence,
                        "seconds": round(time.monotonic() - t0, 1)}
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
                if not isinstance(args, dict):
                    args = {}
                result = tools.dispatch(tc.function.name, args, deadline=deadline)
                evidence.append(_evidence_item(tc.function.name, args, result))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": _clip(result)})

        # Лимит шагов/времени: просим итог по уже полученному, без инструментов.
        if time.monotonic() >= deadline:
            raise TimeoutError("Исчерпан бюджет времени на ответ.")
        if not any(not item.get("error") for item in evidence):
            return {"answer": "Не удалось проверить ответ по базе. / Javobni ma’lumotlar bazasi orqali tekshirib bo‘lmadi.",
                    "error_code": "unverified_answer", "evidence": evidence,
                    "seconds": round(time.monotonic() - t0, 1)}
        messages.append({"role": "user", "content": (
            "Дай итоговый ответ только по уже полученным результатам. Если их недостаточно или SQL завершился "
            "ошибкой — скажи, что ответ не удалось подтвердить. Это не означает отсутствия данных. Ничего не выдумывай.")})
        resp = _chat(client, messages, with_tools=False, deadline=deadline)
        return {"answer": format_answer(resp.choices[0].message.content or "", evidence), "evidence": evidence,
                "seconds": round(time.monotonic() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        error = describe_error(e)
        logger.warning("LLM error [%s]: %s", error["code"], error["detail"])
        return {"answer": error["message"], "error": error["detail"], "error_code": error["code"],
                "evidence": evidence, "seconds": round(time.monotonic() - t0, 1)}
    finally:
        if client is not None:
            client.close()
