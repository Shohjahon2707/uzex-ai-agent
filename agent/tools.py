"""Инструменты агента: безопасный SQL (только чтение) и поиск точных значений."""
import difflib
import os
import re
import sqlite3
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("BIRJA_DB") or (BASE_DIR / "data" / "birja.db"))

MAX_ROWS = 40          # столько строк уходит в LLM и в интерфейс
QUERY_TIMEOUT = 25     # секунд на один SQL
_FORBIDDEN = re.compile(r"\b(attach|detach|pragma|load_extension|vacuum)\b", re.I)


def _lower(s):
    return s.lower() if isinstance(s, str) else s


def connect():
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    # Встроенный lower() в SQLite не понимает кириллицу — подменяем на Unicode-версию.
    conn.create_function("lower", 1, _lower, deterministic=True)
    return conn


def _jsonable(v):
    if isinstance(v, float):
        return round(v, 4)
    return v


def run_sql(query):
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        return {"error": "Пустой запрос."}
    if ";" in q:
        return {"error": "Разрешён только один SQL-запрос без ';' внутри."}
    if not re.match(r"^(select|with)\b", q, re.I):
        return {"error": "Разрешены только SELECT / WITH ... SELECT."}
    if _FORBIDDEN.search(q):
        return {"error": "Запрещённое ключевое слово в запросе."}

    conn = connect()
    deadline = time.time() + QUERY_TIMEOUT
    conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 200_000)
    try:
        cur = conn.execute(q)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
    except sqlite3.Error as e:
        msg = str(e)
        if "interrupted" in msg:
            msg = f"Запрос слишком долгий (> {QUERY_TIMEOUT} c). Упрости, добавь фильтр по дате/контракту."
        return {"error": msg}
    finally:
        conn.close()

    truncated = len(rows) > MAX_ROWS
    rows = [[_jsonable(v) for v in r] for r in rows[:MAX_ROWS]]
    out = {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": truncated}
    if not rows:
        out["note"] = "Запрос вернул 0 строк: по таким условиям данных нет."
    elif all(v is None for r in rows for v in r):
        out["note"] = "Все значения NULL: данных для расчёта нет."
    elif truncated:
        out["note"] = f"Показаны первые {MAX_ROWS} строк; есть ещё. Уточни запрос (LIMIT/агрегат)."
    return out


# ---------------------------------------------------------------- lookup
_APOS = re.compile(r"[ʻʼ’‘`´']")


def _norm(s):
    s = _APOS.sub("", str(s).lower().replace("ё", "е"))
    return re.sub(r"[^\w\s]+", " ", s)


def lookup(table, column, text, limit=15):
    """Ищет в колонке реальные значения, похожие на text (регистр и апострофы не важны)."""
    conn = connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE '\\_%' ESCAPE '\\'")}
        if table not in tables:
            return {"error": f"Нет таблицы {table}. Доступны: {', '.join(sorted(tables))}"}
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        if column not in cols:
            return {"error": f"В {table} нет колонки {column}. Есть: {', '.join(cols)}"}
        src = "contracts" if (table == "deals_v" and column in (
            "product_name", "product_group", "product_category", "industry", "brand", "grade")
            and column in [r[1] for r in conn.execute('PRAGMA table_info("contracts")')]) else table
        deadline = time.time() + QUERY_TIMEOUT
        conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 200_000)
        rows = conn.execute(
            f'SELECT "{column}", COUNT(*) FROM "{src}" WHERE "{column}" IS NOT NULL GROUP BY "{column}"').fetchall()
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()

    tokens = _norm(text).split()
    normed = [(_norm(v), v, c) for v, c in rows]
    hits = [(v, c) for n, v, c in normed if tokens and all(t in n for t in tokens)]
    mode = "substring"
    if not hits and tokens:
        # Нечёткий поиск по словам: Samarkand ~ Samarqand, Tashkent ~ Toshkent.
        mode = "fuzzy"
        scored = []
        for n, v, c in normed[:30000]:
            vt = n.split()
            if not vt:
                continue
            total = 0.0
            for t in tokens:
                best = 0.0
                for w in vt:
                    sm = difflib.SequenceMatcher(None, t, w)
                    if sm.real_quick_ratio() < 0.7 or sm.quick_ratio() < 0.7:
                        continue
                    best = max(best, sm.ratio())
                total += best
            score = total / len(tokens)
            if score >= 0.75:
                scored.append((score, v, c))
        scored.sort(key=lambda x: (-x[0], -x[2]))
        hits = [(v, c) for _, v, c in scored]
        out_hits_sorted = True
    else:
        out_hits_sorted = False
    if not out_hits_sorted:
        hits.sort(key=lambda x: -x[1])
    out = {"query": text, "source": f"{src}.{column}", "match_mode": mode,
           "matches": [{"value": v, "rows": c} for v, c in hits[:limit]], "total_matches": len(hits)}
    if not hits:
        out["note"] = "Похожих значений не найдено — вероятно, такого в данных нет."
    return out


def dispatch(name, args):
    try:
        if name == "run_sql":
            return run_sql(args.get("query", ""))
        if name == "lookup":
            return lookup(args.get("table", ""), args.get("column", ""), args.get("text", ""))
    except Exception as e:  # не роняем весь ответ из-за ошибки инструмента
        return {"error": f"{type(e).__name__}: {e}"}
    return {"error": f"Неизвестный инструмент {name}"}


def schema_summary():
    try:
        conn = connect()
        row = conn.execute("SELECT v FROM _meta WHERE k='schema_summary'").fetchone()
        conn.close()
        return row[0] if row else ""
    except sqlite3.Error:
        return ""
