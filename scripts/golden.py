#!/usr/bin/env python3
"""
Прогон «золотых» вопросов через работающий сервис: проверяет ответы и время.

    python scripts/golden.py scripts/golden.csv http://127.0.0.1:8000

golden.csv: колонки  question ; expected ; mode
  mode=contains — в ответе должны быть все значения из expected (через |); пробелы/запятые в числах не важны
  mode=nodata   — агент должен честно сказать, что данных нет (expected не нужен)
Эталон считай САМ напрямую (pandas / SQL по CSV), не словами агента.
"""
import csv
import json
import re
import sys
import time
import urllib.request

NODATA = ("нет", "yo'q", "yo‘q", "yoq", "topilmadi", "mavjud emas", "не найден", "не найдено")


def norm(s):
    return re.sub(r"[\s  ,]+", "", s.lower())


def ask(base, question):
    req = urllib.request.Request(base.rstrip("/") + "/api/ask", method="POST",
                                 data=json.dumps({"question": question}, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "scripts/golden.csv"
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
    passed = total = 0
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
            mode = (row.get("mode") or "contains").strip()
            if mode == "nodata":
                ok = any(w in ans.lower() for w in NODATA)
            else:
                ok = all(norm(x) in norm(ans) for x in (row.get("expected") or "").split("|") if x.strip())
            ok = ok and dt < 90
            passed += ok
            print(f"{'PASS' if ok else 'FAIL'}  {dt:5.1f}s  {q}")
            if not ok:
                print(f"      ожидали: {row.get('expected') or '(нет данных)'}\n      ответ:   {ans[:300]}")
                for e in res.get("evidence", []):
                    if e.get("sql"):
                        print(f"      SQL: {e['sql'][:300]}")
    print(f"\n{passed}/{total} прошли")


if __name__ == "__main__":
    main()
