import json

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
    question = (payload.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "empty question"}, status=400)
    result = service.answer_question(question[:1500], payload.get("history") or [])
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
