import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import service, tools


@require_GET
def index(request):
    return render(request, "chat.html")


@require_GET
def health(request):
    try:
        n = tools.run_sql("SELECT COUNT(*) FROM deals")["rows"][0][0]
        return JsonResponse({"ok": True, "deals": n})
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
    result = service.answer_question(question[:1500], history)
    failed = bool(result.get("error_code"))
    if not settings.DEBUG:
        result.pop("error", None)
    return JsonResponse(result, status=502 if failed else 200, json_dumps_params={"ensure_ascii": False})
