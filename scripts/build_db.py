#!/usr/bin/env python3
"""
Собирает одну SQLite-базу из CSV реестра сделок биржи.

Запуск (локально, на своём компьютере — НЕ на слабом сервере):
    python scripts/build_db.py --data data --out data/birja.db

В папке --data должны лежать: deals_2022.csv ... deals_2026.csv, contracts.csv,
participants.csv, brokers.csv, usd_rates.csv (имена файлов регистр не важен).

Что делает:
  * читает CSV потоком (память почти не растёт), чистит числа и даты;
  * даты ДД.ММ.ГГГГ -> ISO (ГГГГ-ММ-ДД), числа "1 234 567.89" -> 1234567.89;
  * переименовывает колонки в англ. snake_case (так LLM реже ошибается в SQL);
  * строит view deals_v с готовыми volume, unit_price, amount_uzs, amount_usd и т.д.;
  * печатает отчёт-проверку (ПРИШЛИ ЕГО МНЕ, если там что-то подозрительное).
"""
import argparse
import csv
import glob
import os
import re
import sqlite3
import sys
import time

csv.field_size_limit(10_000_000)

# ----------------------------------------------------------------------------
# Переименование колонок: нормализованный русский заголовок -> имя в БД
# ----------------------------------------------------------------------------
DEALS_MAP = {
    "номер сделки": "deal_id",
    "дата сделки": "deal_date",
    "время сделки": "deal_time",
    "тип торгов": "auction_type",
    "количество": "qty",
    "сумма сделки": "amount",
    "стартовая цена": "start_price",
    "номер контракта": "contract_no",
    "наименование товара": "product_name",
    "лот": "lot",
    "единица измерения": "unit",
    "регион": "region",
    "тип контракта": "contract_type",
    "форма контракта": "contract_form",
    "состояние сделки": "status",
    "инн продавца": "seller_inn",
    "инн покупателя": "buyer_inn",
    "брокер продавца": "seller_broker",
    "брокер покупателя": "buyer_broker",
    "дата оплаты": "pay_date",
    "дата поставки": "delivery_date",
    "поставлено": "delivered",
}

CONTRACTS_MAP = {
    "номер контракта": "contract_no",
    "наименование продукта": "product_name",
    "наименование товара": "product_name",
    "марка": "brand",
    "марка товара": "brand",
    "размер": "size",
    "размер товара": "size",
    "сорт": "grade",
    "сорт товара": "grade",
    "тип": "kind",
    "тип товара": "kind",
    "класс": "item_class",
    "класс товара": "item_class",
    "код тн вэд": "hs_code",
    "группа товара": "product_group",
    "категория товара": "product_category",
    "отрасль промышленности": "industry",
    "единица измерения": "unit",
    "лот": "lot",
    "валюта": "currency",
    "базисная цена": "base_price",
    "сумма залога": "deposit_pct",
    "базис поставки": "delivery_basis",
    "упаковка": "packaging",
    "регион": "region",
    "адрес склада": "warehouse_address",
    "тип торгов": "auction_type",
    "тип контракта": "contract_type",
    "форма контракта": "contract_form",
    "срок оплаты": "pay_days",
    "срок поставки": "delivery_days",
    "инн продавца": "seller_inn",
    "дата регистрации": "reg_date",
    "действует до": "valid_until",
}

PARTICIPANTS_MAP = {
    "инн": "inn",
    "наименование": "name",
    "название": "name",
    "регион": "region",
    "вид деятельности": "activity",
    "роль": "role",
    "плательщик ндс": "vat_payer",
}

BROKERS_MAP = {
    "код брокера": "broker_code",
    "наименование": "name",
    "инн": "inn",
    "регион": "region",
}

NUMERIC = {"qty", "amount", "start_price", "lot", "delivered", "base_price",
           "deposit_pct", "pay_days", "delivery_days", "rate"}
INTEGER = {"deal_id"}
DATES = {"deal_date", "pay_date", "delivery_date", "reg_date", "valid_until", "rate_date"}
INNS = {"seller_inn", "buyer_inn", "inn"}

_WS = re.compile(r"[\s    ]+")
_DMY = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def norm_header(h):
    h = h.replace("﻿", "").strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", h)


def slug(h):
    s = "".join(_CYR.get(ch, ch) for ch in norm_header(h))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return "x_" + (s or "col")


class Counter(dict):
    def bump(self, key):
        self[key] = self.get(key, 0) + 1


BAD = Counter()  # (table.column) -> число значений, которые не удалось разобрать


def conv_text(s):
    s = s.strip()
    return s if s != "" else None


def conv_inn(s):
    s = s.strip()
    if not s:
        return None
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s


def to_num(s):
    s = _WS.sub("", s).replace("%", "")
    if s == "" or s.lower() in ("-", "nan", "none", "null"):
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return "BAD"


def to_date(s):
    s = s.strip()
    if not s:
        return None
    m = _DMY.match(s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = _ISO.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return "BAD"


def make_converter(target, table):
    def wrap(fn):
        def inner(s):
            v = fn(s)
            if v == "BAD":
                BAD.bump(f"{table}.{target}")
                return None
            return v
        return inner

    if target in INTEGER:
        def f(s):
            v = to_num(s)
            return v if v in (None, "BAD") else int(v)
        return wrap(f)
    if target in NUMERIC:
        return wrap(to_num)
    if target in DATES:
        return wrap(to_date)
    if target in INNS:
        return conv_inn
    return conv_text


def sql_type(target):
    if target in INTEGER:
        return "INTEGER"
    if target in NUMERIC:
        return "NUMERIC"
    return "TEXT"


def find_file(folder, pattern):
    for p in glob.glob(os.path.join(folder, "*")):
        if re.fullmatch(pattern, os.path.basename(p), flags=re.I):
            yield p


def read_header(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def map_columns(headers, mapping, table):
    """Возвращает список целевых имён для каждой колонки CSV."""
    targets, used = [], set()
    for h in headers:
        t = mapping.get(norm_header(h))
        if t is None:
            t = slug(h)
            print(f"  [!] {table}: колонка «{h}» не в карте — сохранена как {t}")
        base, i = t, 2
        while t in used:
            t = f"{base}_{i}"
            i += 1
        used.add(t)
        targets.append(t)
    return targets


def load_table(conn, table, paths, mapping, special_targets=None):
    """Грузит один или несколько CSV с одинаковой структурой в таблицу."""
    first_headers = read_header(paths[0])
    targets = special_targets(first_headers) if special_targets else map_columns(first_headers, mapping, table)
    cols_sql = ", ".join(f'"{t}" {sql_type(t)}' for t in targets)
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(f'CREATE TABLE "{table}" ({cols_sql})')
    convs = [make_converter(t, table) for t in targets]
    ph = ", ".join("?" for _ in targets)
    ncol = len(targets)
    total = 0
    for path in paths:
        t0 = time.time()
        n = 0
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            headers = next(reader)
            if [norm_header(h) for h in headers] != [norm_header(h) for h in first_headers]:
                print(f"  [!] {os.path.basename(path)}: заголовки отличаются от первого файла — проверь вручную")
            batch = []
            for row in reader:
                if not row or all(not c.strip() for c in row):
                    continue
                if len(row) < ncol:
                    row = row + [""] * (ncol - len(row))
                batch.append([convs[i](row[i]) for i in range(ncol)])
                if len(batch) >= 20000:
                    conn.executemany(f'INSERT INTO "{table}" VALUES ({ph})', batch)
                    n += len(batch)
                    batch = []
            if batch:
                conn.executemany(f'INSERT INTO "{table}" VALUES ({ph})', batch)
                n += len(batch)
        conn.commit()
        total += n
        print(f"  {os.path.basename(path)}: {n:,} строк ({time.time() - t0:.1f} c)")
    print(f"  => {table}: {total:,} строк, колонок {ncol}")
    return targets


def usd_targets(headers):
    """usd_rates.csv: две колонки — дата и курс."""
    targets = []
    date_idx = None
    for i, h in enumerate(headers):
        nh = norm_header(h)
        if "дат" in nh or "date" in nh:
            date_idx = i
            break
    if date_idx is None:
        date_idx = 0
    for i, h in enumerate(headers):
        if i == date_idx:
            targets.append("rate_date")
        elif "rate" in targets:
            targets.append(slug(h))
        else:
            targets.append("rate")
    return targets


def table_cols(conn, name):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]


def build_view(conn):
    d = set(table_cols(conn, "deals"))
    c = set(table_cols(conn, "contracts"))
    need = {"deal_date", "qty", "lot", "amount", "contract_no"}
    if not need <= d:
        sys.exit(f"В deals нет колонок {need - d}. Пришли мне заголовки deals_2022.csv.")
    if "currency" not in c:
        print("  [!] в contracts нет currency — суммы в сумах будут считаться по типу контракта")

    deal_cols = [x for x in [
        "deal_id", "deal_date", "deal_time", "auction_type", "contract_form", "contract_type", "status",
        "qty", "lot", "unit", "amount", "start_price", "contract_no", "product_name", "region",
        "seller_inn", "buyer_inn", "seller_broker", "buyer_broker", "pay_date", "delivery_date", "delivered",
    ] if x in d]
    sel = [f"d.{x}" for x in deal_cols]
    sel.append("(d.qty * d.lot) AS volume")
    sel.append("d.amount / NULLIF(d.qty * d.lot, 0) AS unit_price")

    if "currency" in c:
        sel.append("k.currency AS currency")
        is_usd = ("CASE WHEN instr(IFNULL(k.currency,''), 'Доллар') > 0 THEN 1 "
                  "WHEN k.currency IS NULL AND d.contract_type = 'Экспортный' THEN 1 ELSE 0 END")
    else:
        is_usd = "CASE WHEN d.contract_type = 'Экспортный' THEN 1 ELSE 0 END"
    sel.append(f"({is_usd}) AS is_usd")
    sel.append("r.rate AS usd_rate")
    sel.append(f"CASE WHEN ({is_usd}) = 1 THEN d.amount * r.rate ELSE d.amount END AS amount_uzs")
    sel.append(f"CASE WHEN ({is_usd}) = 1 THEN d.amount ELSE d.amount / NULLIF(r.rate, 0) END AS amount_usd")

    for col in ["product_group", "product_category", "industry", "hs_code", "brand", "size", "grade",
                "kind", "item_class", "pay_days", "delivery_days", "deposit_pct", "delivery_basis",
                "packaging", "base_price"]:
        if col in c:
            sel.append(f"k.{col} AS {col}")

    p = set(table_cols(conn, "participants"))
    if {"inn", "name"} <= p:
        sel.append("(SELECT p.name FROM participants p WHERE p.inn = d.seller_inn LIMIT 1) AS seller_name")
        sel.append("(SELECT p.name FROM participants p WHERE p.inn = d.buyer_inn LIMIT 1) AS buyer_name")
        if "region" in p:
            sel.append("(SELECT p.region FROM participants p WHERE p.inn = d.seller_inn LIMIT 1) AS seller_region")
            sel.append("(SELECT p.region FROM participants p WHERE p.inn = d.buyer_inn LIMIT 1) AS buyer_region")
    b = set(table_cols(conn, "brokers"))
    if {"broker_code", "name"} <= b:
        sel.append("(SELECT b.name FROM brokers b WHERE b.broker_code = d.seller_broker LIMIT 1) AS seller_broker_name")
        sel.append("(SELECT b.name FROM brokers b WHERE b.broker_code = d.buyer_broker LIMIT 1) AS buyer_broker_name")

    sql = "CREATE VIEW deals_v AS SELECT " + ", ".join(sel) + \
          " FROM deals d LEFT JOIN contracts k ON k.contract_no = d.contract_no" \
          " LEFT JOIN usd_rates r ON r.rate_date = d.deal_date"
    conn.execute("DROP VIEW IF EXISTS deals_v")
    conn.execute(sql)
    conn.commit()


def dedupe(conn, table, key):
    n = conn.execute(f'SELECT COUNT(*) FROM (SELECT "{key}" FROM "{table}" GROUP BY "{key}" HAVING COUNT(*) > 1)').fetchone()[0]
    if n:
        print(f"  [!] {table}: {n} дублей по {key} — оставляю первую запись (иначе суммы в view задвоятся)")
        conn.execute(f'DELETE FROM "{table}" WHERE rowid NOT IN (SELECT MIN(rowid) FROM "{table}" GROUP BY "{key}")')
        conn.commit()
    return n


def make_indexes(conn):
    def idx(name, table, col, unique=False):
        if col not in table_cols(conn, table):
            return
        conn.execute(f'DROP INDEX IF EXISTS {name}')
        conn.execute(f'CREATE {"UNIQUE " if unique else ""}INDEX {name} ON "{table}"("{col}")')

    for name, col in [("i_deals_date", "deal_date"), ("i_deals_contract", "contract_no"),
                      ("i_deals_seller", "seller_inn"), ("i_deals_buyer", "buyer_inn"),
                      ("i_deals_status", "status"), ("i_deals_id", "deal_id"),
                      ("i_deals_sbroker", "seller_broker"), ("i_deals_bbroker", "buyer_broker")]:
        idx(name, "deals", col)
    idx("u_contracts_no", "contracts", "contract_no", unique=True)
    idx("i_part_inn", "participants", "inn")
    idx("i_brokers_code", "brokers", "broker_code")
    idx("u_usd_date", "usd_rates", "rate_date", unique=True)
    conn.commit()


def distinct_list(conn, table, col, limit=40):
    if col not in table_cols(conn, table):
        return None
    rows = conn.execute(
        f'SELECT "{col}", COUNT(*) c FROM "{table}" WHERE "{col}" IS NOT NULL GROUP BY "{col}" ORDER BY c DESC LIMIT {limit + 1}'
    ).fetchall()
    if len(rows) > limit:
        return None
    return [r[0] for r in rows]


def build_schema_summary(conn):
    lines = []
    for name, kind in conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE '\\_%' ESCAPE '\\' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY (name='deals_v') DESC, name").fetchall():
        cols = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
        cnt = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] if kind == "table" else None
        head = f"{kind.upper()} {name}" + (f" ({cnt:,} строк)" if cnt is not None else " (основное представление сделок: сделка + контракт + курс + названия)")
        lines.append(head)
        lines.append("  колонки: " + ", ".join(f"{c[1]} {c[2] or ''}".strip() for c in cols))
    lines.append("")
    dmin, dmax = conn.execute("SELECT MIN(deal_date), MAX(deal_date) FROM deals").fetchone()
    lines.append(f"Период сделок в данных: {dmin} … {dmax}.")
    lines.append("")
    lines.append("Допустимые значения справочных полей (используй ТОЧНО такие строки):")
    for table, col in [("deals", "status"), ("deals", "auction_type"), ("deals", "contract_type"),
                       ("deals", "contract_form"), ("deals", "unit"), ("deals", "region"),
                       ("contracts", "currency"), ("contracts", "product_group"),
                       ("contracts", "product_category"), ("contracts", "industry"),
                       ("participants", "role"), ("participants", "vat_payer"), ("participants", "region"),
                       ("brokers", "region")]:
        vals = distinct_list(conn, table, col)
        if vals:
            lines.append(f"  {table}.{col}: " + " | ".join(str(v) for v in vals))
    return "\n".join(lines)


def report(conn):
    q = lambda s: conn.execute(s).fetchall()
    print("\n" + "=" * 70 + "\nОТЧЁТ-ПРОВЕРКА (сверь с README и пришли мне, если что-то странно)\n" + "=" * 70)
    n = q("SELECT COUNT(*) FROM deals")[0][0]
    print(f"Сделок всего: {n:,}   (в README: 500 000)")
    print("По годам:", q("SELECT substr(deal_date,1,4) y, COUNT(*) FROM deals GROUP BY y ORDER BY y"))
    print("README: 2022: 84299 · 2023: 93985 · 2024: 106647 · 2025: 115763 · 2026: 99306")
    print("Период:", q("SELECT MIN(deal_date), MAX(deal_date) FROM deals"))
    print("Статусы:", q("SELECT status, COUNT(*) FROM deals GROUP BY status ORDER BY 2 DESC"))
    print("Тип контракта × валюта контракта:")
    for r in q("SELECT d.contract_type, k.currency, COUNT(*) FROM deals d LEFT JOIN contracts k "
               "ON k.contract_no = d.contract_no GROUP BY 1, 2 ORDER BY 3 DESC"):
        print("   ", r)
    miss_k = q("SELECT COUNT(*) FROM deals d WHERE NOT EXISTS (SELECT 1 FROM contracts k WHERE k.contract_no = d.contract_no)")[0][0]
    print(f"Сделок без карточки контракта: {miss_k:,}")
    miss_r = q("SELECT COUNT(*) FROM deals_v WHERE is_usd = 1 AND usd_rate IS NULL")[0][0]
    print(f"Долларовых сделок без курса на дату: {miss_r:,}")
    nulls = q("SELECT SUM(amount IS NULL), SUM(qty IS NULL), SUM(lot IS NULL), SUM(deal_date IS NULL) FROM deals")[0]
    print(f"NULL в deals: amount={nulls[0]}, qty={nulls[1]}, lot={nulls[2]}, deal_date={nulls[3]}")
    dup = q("SELECT COUNT(*) FROM (SELECT deal_id FROM deals GROUP BY deal_id HAVING COUNT(*) > 1)")[0][0]
    print(f"Повторяющихся номеров сделки: {dup}")
    if BAD:
        print("НЕ РАЗОБРАНО значений (стали NULL):", dict(BAD))
    print("Пример deals_v (3 строки):")
    cur = conn.execute("SELECT * FROM deals_v LIMIT 3")
    names = [d[0] for d in cur.description]
    for row in cur.fetchall():
        print("   ", dict(zip(names, row)))
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="папка с CSV")
    ap.add_argument("--out", default="data/birja.db", help="куда писать базу")
    args = ap.parse_args()

    deals = sorted(find_file(args.data, r"deals_\d{4}\.csv"))
    one = lambda pat: next(iter(find_file(args.data, pat)), None)
    contracts, participants = one(r"contracts\.csv"), one(r"participants\.csv")
    brokers, usd = one(r"brokers\.csv"), one(r"usd_rates\.csv")
    missing = [n for n, p in [("deals_*.csv", deals), ("contracts.csv", contracts), ("participants.csv", participants),
                              ("brokers.csv", brokers), ("usd_rates.csv", usd)] if not p]
    if missing:
        sys.exit(f"В папке {args.data} не найдены: {', '.join(missing)}")

    if os.path.exists(args.out):
        os.remove(args.out)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    conn = sqlite3.connect(args.out)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    t0 = time.time()

    print("contracts"); load_table(conn, "contracts", [contracts], CONTRACTS_MAP)
    print("participants"); load_table(conn, "participants", [participants], PARTICIPANTS_MAP)
    print("brokers"); load_table(conn, "brokers", [brokers], BROKERS_MAP)
    print("usd_rates"); load_table(conn, "usd_rates", [usd], {}, special_targets=usd_targets)
    print("deals"); load_table(conn, "deals", deals, DEALS_MAP)

    dedupe(conn, "contracts", "contract_no")
    dedupe(conn, "usd_rates", "rate_date")
    make_indexes(conn)
    build_view(conn)

    conn.execute("DROP TABLE IF EXISTS _meta")
    conn.execute("CREATE TABLE _meta (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO _meta VALUES ('schema_summary', ?)", (build_schema_summary(conn),))
    conn.commit()
    conn.execute("ANALYZE")
    conn.commit()
    report(conn)
    conn.close()
    size = os.path.getsize(args.out) / 1e6
    print(f"\nГотово за {time.time() - t0:.0f} c. Файл {args.out}: {size:.0f} МБ")


if __name__ == "__main__":
    main()
