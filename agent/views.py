import json
import logging
import threading

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import service, tools
from .llm import describe_error

logger = logging.getLogger(__name__)
RESPONSE_TIMEOUT = 80
_answer_slots = threading.BoundedSemaphore(2)


def _bounded_answer(question, history):
    """Ограничиваем ожидание HTTP-ответа, даже если SDK завис внутри сетевого вызова."""
    slots = _answer_slots
    if not slots.acquire(blocking=False):
        return {"answer": "Сервис занят. Повторите вопрос чуть позже. / Xizmat band. Birozdan so‘ng qayta urinib ko‘ring.",
                "error_code": "busy"}, 503
    finished = threading.Event()
    holder = {}

    def run():
        try:
            holder["result"] = service.answer_question(question, history)
        except Exception as exc:
            error = describe_error(exc)
            logger.warning("Answer failed [%s]: %s", error["code"], error["detail"])
            holder["result"] = {"answer": error["message"], "error_code": error["code"], "error": error["detail"]}
        finally:
            slots.release()
            finished.set()

    worker = threading.Thread(target=run, name="birja-answer", daemon=True)
    try:
        worker.start()
    except Exception:
        slots.release()
        raise
    if not finished.wait(RESPONSE_TIMEOUT):
        return {"answer": "Расчёт не завершился вовремя. Уточните вопрос или повторите попытку. / Hisob-kitob belgilangan vaqtda yakunlanmadi. Savolni aniqlashtiring yoki qayta urinib ko‘ring.",
                "error_code": "timeout", "seconds": RESPONSE_TIMEOUT}, 504
    result = holder["result"]
    return result, 502 if result.get("error_code") else 200


@require_GET
def index(request):
    return render(request, "chat.html")


@require_GET
def health(request):
    try:
        n, start, end = tools.run_sql(
            "SELECT COUNT(*), MIN(deal_date), MAX(deal_date) FROM deals"
        )["rows"][0]
        return JsonResponse({"ok": True, "deals": n, "date_from": start, "date_to": end})
    except Exception as e:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_POST
def ask(request):
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "bad json"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "expected JSON object"}, status=400)
    question = payload.get("question", "")
    if not isinstance(question, str):
        return JsonResponse({"error": "question must be a string"}, status=400)
    question = question.strip()
    if not question:
        return JsonResponse({"error": "empty question"}, status=400)
    history = payload.get("history") or []
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        return JsonResponse({"error": "history must be a list of messages"}, status=400)
    result, status = _bounded_answer(question[:1500], history)
    if not settings.DEBUG:
        result.pop("error", None)
    return JsonResponse(result, status=status, json_dumps_params={"ensure_ascii": False})
