#!/usr/bin/env python3
"""Независимая проверка расчётов SQLite по исходным CSV, без LLM и API-ключа."""
import argparse
import csv
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import tools


def number(value):
    return Decimal("".join(value.split()).replace(",", "."))


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        yield from csv.DictReader(file)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data", type=Path)
    parser.add_argument("--db", default="data/birja.db", type=Path)
    args = parser.parse_args()
    tools.DB_PATH = args.db
    files = sorted(args.data.glob("deals_*.csv"))
    if not files:
        parser.error("В --data не найдены deals_*.csv")
    currencies = {r["Номер контракта"]: r["Валюта"] for r in rows(args.data / "contracts.csv")}
    rate_rows = list(rows(args.data / "usd_rates.csv"))
    date_key, rate_key = rate_rows[0].keys()
    rates = {r[date_key]: number(r[rate_key]) for r in rate_rows}
    years, statuses, brokers = Counter(), Counter(), Counter()
    volumes = defaultdict(Decimal)
    total, export_count, export_sum = Decimal(0), 0, Decimal(0)
    for file in files:
        for row in rows(file):
            years[row["Дата сделки"][-4:]] += 1
            statuses[row["Состояние сделки"]] += 1
            amount = number(row["Сумма сделки"])
            if currencies[row["Номер контракта"]] == "Доллар США":
                total += amount * rates[row["Дата сделки"]]
                export_count += 1
                export_sum += amount
            else:
                total += amount
            volumes[row["Единица измерения"]] += number(row["Количество"]) * number(row["Лот"])
            for broker in {row["Брокер продавца"], row["Брокер покупателя"]}:
                brokers[broker] += 1
    checks = [
        ("Число сделок", "SELECT COUNT(*) FROM deals", [[sum(years.values())]]),
        ("Сделки по годам", "SELECT substr(deal_date,1,4),COUNT(*) FROM deals GROUP BY 1 ORDER BY 1", [list(x) for x in sorted(years.items())]),
        ("Статусы", "SELECT status,COUNT(*) FROM deals GROUP BY 1 ORDER BY 1", [list(x) for x in sorted(statuses.items())]),
        ("Экспорт", "SELECT COUNT(*),sum_money(amount) FROM deals_v WHERE is_usd=1", [[export_count, export_sum]]),
        ("Точная сумма в сумах по курсу каждой сделки", "SELECT sum_uzs(amount,usd_rate,is_usd) FROM deals_v", [[total]]),
        ("Объёмы отдельно по единицам", "SELECT unit,SUM(qty*lot) FROM deals GROUP BY 1 ORDER BY 1", [list(x) for x in sorted(volumes.items())]),
        ("Брокеры на обеих сторонах без двойного учёта", "WITH sides AS (SELECT deal_id,seller_broker AS broker_code FROM deals UNION SELECT deal_id,buyer_broker FROM deals) SELECT broker_code,COUNT(*) AS n FROM sides GROUP BY broker_code ORDER BY n DESC,broker_code LIMIT 5", [list(x) for x in sorted(brokers.items(), key=lambda x: (-x[1], x[0]))[:5]]),
    ]
    failures = 0
    for title, query, expected in checks:
        result = tools.run_sql(query)
        actual = result.get("rows")
        if actual and len(actual) == len(expected):
            actual = [[Decimal(str(value)) if isinstance(want, Decimal) else value for value, want in zip(row, reference)] for row, reference in zip(actual, expected)]
        ok = actual == expected
        print(f"{'PASS' if ok else 'FAIL'} {title}")
        if not ok:
            failures += 1
            print(f"  CSV: {expected}\n  SQL: {result}")
    print(f"\n{len(checks) - failures}/{len(checks)} независимых проверок. Это проверка расчётов, не качества ответов LLM.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
