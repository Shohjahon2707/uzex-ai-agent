"""Настройки и безопасная диагностика OpenAI-совместимого API."""
import os
import re
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    key_name: str = "LLM_API_KEY"


_rotation_lock = threading.Lock()
_rotation_signature = ()
_rotation_position = 0


def key_pool():
    """Нумерованные ключи имеют приоритет; дубликаты не создают новые слоты."""
    numbered = sorted((int(match[1]), name, value.strip()) for name, value in os.environ.items()
                      if (match := re.fullmatch(r"LLM_API_KEY_([1-9]\d*)", name)) and value.strip())
    candidates = [(name, value) for _, name, value in numbered]
    if not candidates:
        candidates = [(name, os.environ.get(name, "").strip()) for name in ("LLM_API_KEY", "GROQ_API_KEY")]
    result, seen = [], set()
    for name, value in candidates:
        if not value or value in seen:
            continue
        if value.startswith("your_") or value == "твой_ключ_из_AI_Studio":
            raise ConfigurationError(f"В {name} остался плейсхолдер. Впиши ключ или убери эту строку из .env.")
        result.append((name, value))
        seen.add(value)
        # Одиночный LLM_API_KEY имеет приоритет над GROQ_API_KEY.
        if not numbered:
            break
    if not result:
        raise ConfigurationError("В .env не задан LLM_API_KEY или LLM_API_KEY_1. Сохрани файл рядом с manage.py и перезапусти сервер.")
    return result


def get_config(key_index=0):
    pool = key_pool()
    if key_index < 0 or key_index >= len(pool):
        raise ConfigurationError(f"Номер ключа должен быть от 1 до {len(pool)}.")
    key_name, key = pool[key_index]
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    if not base_url:
        base_url = "https://api.groq.com/openai/v1" if key_name == "GROQ_API_KEY" or key.startswith("gsk_") else "https://api.openai.com/v1"
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ConfigurationError("LLM_BASE_URL должен быть адресом API без ключей и параметров в URL.")
    if parsed.path.rstrip("/").endswith("/chat/completions"):
        raise ConfigurationError("Убери /chat/completions из LLM_BASE_URL: SDK добавляет этот путь сам.")
    default_model = "openai/gpt-oss-20b" if parsed.hostname == "api.groq.com" else "gpt-4.1-mini"
    model = os.environ.get("LLM_MODEL", "").strip() or default_model
    return LLMConfig(key, base_url.rstrip("/"), model, key_name)


def next_config():
    """Один выбор на вопрос. Вызовы инструментов используют тот же клиент/ключ."""
    global _rotation_signature, _rotation_position
    pool = key_pool()
    signature = tuple(pool)
    with _rotation_lock:
        if signature != _rotation_signature:
            _rotation_signature, _rotation_position = signature, 0
        position = _rotation_position % len(pool)
        config = get_config(key_index=position)
        _rotation_position += 1
    return config


def create_client(timeout=30, config=None):
    from openai import OpenAI

    config = config or next_config()
    return OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=timeout, max_retries=0)


def redact(text):
    text = str(text)
    values = set()
    for name, value in os.environ.items():
        if name not in {"LLM_API_KEY", "GROQ_API_KEY"} and not re.fullmatch(r"LLM_API_KEY_[1-9]\d*", name):
            continue
        key = value.strip()
        if key:
            values.add(key)
    for key in sorted(values, key=len, reverse=True):
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
        401: ("invalid_api_key", "Провайдер модели не принимает выбранный API-ключ (401). Проверь соответствующую строку LLM_API_KEY в .env и перезапусти сервер."),
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
