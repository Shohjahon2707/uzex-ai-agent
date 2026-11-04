#!/usr/bin/env python3
"""
Прогон «золотых» вопросов через работающий сервис: проверяет ответы и время.

    python scripts/golden.py scripts/golden.csv http://127.0.0.1:8000

golden.csv: колонки question,expected,mode (CSV с запятой)
  mode=contains — в ответе должны быть все значения из expected (через |); пробелы/запятые в числах не важны
  mode=nodata   — агент должен честно сказать, что данных нет (expected не нужен)
Эталон считай САМ напрямую (pandas / SQL по CSV), не словами агента.
"""
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal

NODATA = ("нет", "yo'q", "yo‘q", "yoq", "topilmadi", "mavjud emas", "не найден", "не найдено")


def norm(s):
    return re.sub(r"[\s  ,]+", "", s.lower())


def contains_value(answer, expected):
    value = norm(expected)
    if re.fullmatch(r"-?\d+(\.\d+)?", value):
        pattern = r"(?<![\w\d])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?)(?![\w\d])"
        for match in re.finditer(pattern, answer):
            token = re.sub(r"\s", "", match.group())
            if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", token):
                token = token.replace(",", "")
            else:
                token = token.replace(",", ".")
            if Decimal(token) == Decimal(value):
                return True
        return False
    return value in norm(answer)


def ask(base, question):
    req = urllib.request.Request(base.rstrip("/") + "/api/ask", method="POST",
                                 data=json.dumps({"question": question}, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "scripts/golden.csv"
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
    passed = total = 0
    blocked = False
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            q = (row.get("question") or "").strip()
            if not q or q.startswith("#"):
                continue
            total += 1
            t = time.time()
            try:
                res = ask(base, q)
                ans = res.get("answer", "")
            except Exception as e:  # noqa: BLE001
                ans, res = f"ERROR {e}", {}
            dt = time.time() - t
            if res.get("error_code") == "rate_limit":
                total -= 1
                blocked = True
                print(f"BLOCKED  {dt:5.1f}s  {q}\n      {ans}")
                print("Остановлено на квоте провайдера. Остальные вопросы не проверялись; это не ошибки точности.")
                break
            mode = (row.get("mode") or "contains").strip()
            if mode == "nodata":
                ok = any(w in ans.lower() for w in NODATA)
            else:
                ok = all(contains_value(ans, x) for x in (row.get("expected") or "").split("|") if x.strip())
            ok = ok and dt < 90 and not res.get("error_code")
            passed += ok
            print(f"{'PASS' if ok else 'FAIL'}  {dt:5.1f}s  {q}")
            if not ok:
                print(f"      ожидали: {row.get('expected') or '(нет данных)'}\n      ответ:   {ans[:300]}")
                for e in res.get("evidence", []):
                    if e.get("sql"):
                        print(f"      SQL: {e['sql'][:300]}")
    print(f"\n{passed}/{total} прошли")
    return 2 if blocked else int(passed != total)


if __name__ == "__main__":
    raise SystemExit(main())
