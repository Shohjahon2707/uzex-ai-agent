"""Обновление локального .env без публикации ключа или потери остальных настроек."""
import os
import re
import tempfile
from pathlib import Path


def configure_deepseek(env_path, key, model="deepseek-v4-pro"):
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", key):
        raise ValueError("Ключ пустой или содержит пробелы/переводы строк.")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", model):
        raise ValueError("Некорректное имя модели.")
    path = Path(env_path)
    settings = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": key,
                "DEEPSEEK_MODEL": model, "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"}
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    kept = [line for line in lines if line.strip().split("=", 1)[0].strip() not in settings]
    text = "\n".join(kept + [f"{name}={value}" for name, value in settings.items()]) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".env.", suffix=".tmp", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
