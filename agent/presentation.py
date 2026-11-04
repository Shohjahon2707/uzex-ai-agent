"""Форматируем только числа, действительно полученные из инструментов."""
import re


IDENTIFIER = re.compile(r"(^|_)(id|inn|code|no|year|date|broker)(_|$)|номер|инн|код|год|дата", re.I)


def format_answer(text, evidence):
    replacements = {}
    for item in evidence:
        if item.get("tool") != "run_sql" or item.get("error"):
            continue
        for row in item.get("rows", []):
            for column, value in zip(item.get("columns", []), row):
                if IDENTIFIER.search(column) or isinstance(value, bool):
                    continue
                if isinstance(value, str):
                    if not re.search(r"сумма|sum_money|sum_uzs|amount|total|summa", column, re.I) or not re.fullmatch(r"-?\d+(\.\d+)?", value):
                        continue
                    raw = value
                elif isinstance(value, (int, float)):
                    raw = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
                else:
                    continue
                integer, dot, fraction = raw.partition(".")
                if "e" in raw.lower():
                    continue
                if len(integer.lstrip("-")) < 4:
                    continue
                negative = integer.startswith("-")
                integer = integer.lstrip("-")
                grouped = f"{int(integer):,}".replace(",", " ")
                replacements[raw] = ("-" if negative else "") + grouped + dot + fraction
    for raw in sorted(replacements, key=len, reverse=True):
        text = re.sub(r"(?<![\w.,:/-])" + re.escape(raw) + r"(?![\w:/-]|[.,]\d)", replacements[raw], text)
    return text.strip()
