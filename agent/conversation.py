"""Передаём историю только для явного продолжения, не для нового вопроса."""
import re


def needs_history(question):
    text = question.strip().lower().replace("ё", "е")
    text = text.translate(str.maketrans({char: "'" for char in "ʻʼ’‘`´"}))
    text = re.sub(r"\s+", " ", text)
    return bool(
        re.search(r"^(а\b|теперь\b|тогда\b|еще\b|также\b|shu\b|endi\b|yana\b)", text)
        or re.search(r"\b(из них|среди них|по ним|для них|у них|этих|этим|этого|этой|того|той|тех|предыдущ\w*|тот же|те же|ular(?:ning|dan)?|shular(?:ning|dan)?|avvalgi|o'sha)\b", text)
        or re.search(r"\b\d{4}[- ]?yilda[- ]?chi\b", text)
    )


def relevant_history(question, history):
    """Последний самостоятельный вопрос — граница контекста, более старые фильтры не нужны."""
    if not needs_history(question):
        return []
    valid = [item for item in (history or []) if isinstance(item, dict)
             and item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)]
    for index in range(len(valid) - 1, -1, -1):
        item = valid[index]
        if item["role"] == "user" and not needs_history(item["content"]):
            return valid[index:]
    return []
