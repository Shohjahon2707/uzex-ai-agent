"""Настройки и безопасная диагностика OpenAI-совместимого API."""
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str


def get_config():
    key = os.environ.get("LLM_API_KEY", "").strip()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        key = groq_key
    if not key or key in {"your_api_key_here", "твой_ключ_из_AI_Studio"}:
        raise ConfigurationError(
            "В .env не задан настоящий LLM_API_KEY. Сохрани файл рядом с manage.py и перезапусти сервер."
        )
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    if not base_url:
        base_url = "https://api.groq.com/openai/v1" if groq_key else "https://api.openai.com/v1"
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ConfigurationError("LLM_BASE_URL должен быть адресом API без ключей и параметров в URL.")
    if parsed.path.rstrip("/").endswith("/chat/completions"):
        raise ConfigurationError("Убери /chat/completions из LLM_BASE_URL: SDK добавляет этот путь сам.")
    default_model = "openai/gpt-oss-120b" if parsed.hostname == "api.groq.com" else "gpt-4.1-mini"
    model = os.environ.get("LLM_MODEL", "").strip() or default_model
    return LLMConfig(key, base_url.rstrip("/"), model)


def create_client(timeout=30, config=None):
    from openai import OpenAI

    config = config or get_config()
    return OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=timeout, max_retries=0)


def redact(text):
    text = str(text)
    for name in ("LLM_API_KEY", "GROQ_API_KEY"):
        key = os.environ.get(name, "").strip()
        if key:
            text = text.replace(key, "[REDACTED]")
    return re.sub(r"(?:gsk_|sk-|AIza|AQ\.)[A-Za-z0-9_.-]+", "[REDACTED]", text)


def describe_error(exc):
    """Сообщение для чата и очищенные детали для локальной диагностики."""
    from openai import APIConnectionError, APITimeoutError

    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    detail = body if body is not None else str(exc)
    detail = redact(f"{type(exc).__name__}: {detail}")[:3000]
    messages = {
        400: ("invalid_request", "Провайдер модели отклонил параметры запроса (400). Выполни python manage.py check_llm."),
        401: ("invalid_api_key", "Провайдер модели не принимает API-ключ (401). Обнови LLM_API_KEY в .env и перезапусти сервер."),
        403: ("access_denied", "Нет доступа к API модели (403). Проверь разрешения ключа, проект и доступность сервиса в регионе."),
        404: ("model_not_found", "Не найден адрес API или модель (404). Проверь LLM_BASE_URL и LLM_MODEL; список: python manage.py check_llm --list-models."),
        429: ("rate_limit", "Достигнут лимит запросов или квоты модели (429). Проверь квоту у провайдера или повтори позже."),
    }
    if isinstance(exc, ConfigurationError):
        code, message = "configuration", str(exc)
    elif isinstance(exc, (APITimeoutError, TimeoutError)):
        code, message = "timeout", "Модель не успела ответить. Попробуй ещё раз. / Model javob berishga ulgurmadi, qayta urinib ko‘ring."
    elif isinstance(exc, APIConnectionError):
        code, message = "connection", "Не удалось подключиться к API модели. Проверь соединение сервера с провайдером. / Model API bilan aloqa yo‘q."
    elif status in messages:
        code, message = messages[status]
    elif status is not None and status >= 500:
        code, message = "provider_unavailable", f"Сервис модели временно недоступен ({status}). Повтори позже. / Model xizmati vaqtincha ishlamayapti."
    else:
        code, message = "internal_error", "Не удалось обработать вопрос. Подробности записаны в терминале сервера. / Savolni qayta ishlashda xatolik yuz berdi."
    return {"code": code, "message": message, "detail": detail}
