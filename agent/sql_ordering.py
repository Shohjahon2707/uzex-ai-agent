"""Денежные Decimal-строки должны сравниваться численно до ORDER BY / LIMIT."""
import sqlparse
from sqlparse import tokens as T
from sqlparse.sql import Function, TokenList


MONEY_FUNCTIONS = {"sum_money", "sum_uzs"}


def _contains_money(token):
    if isinstance(token, Function) and (token.get_name() or "").lower() in MONEY_FUNCTIONS:
        return True
    return isinstance(token, TokenList) and any(_contains_money(child) for child in token.tokens)


def with_decimal_collation(query):
    """Устанавливаем collation на выражении: она наследуется алиасом и CTE.

    Числовая сортировка происходит в самой SQLite перед LIMIT. Сортировка уже
    выбранных первых трёх строк в Python не вернула бы потерянный четвёртый регион.
    Строки, комментарии и имена обычных колонок не переписываются.
    """
    statements = sqlparse.parse(query)
    leaves = [token for statement in statements for token in statement.flatten()]
    positions, offset = {}, 0
    for index, token in enumerate(leaves):
        positions[id(token)] = (index, offset, offset + len(token.value))
        offset += len(token.value)
    edits = []

    def next_significant(index):
        index += 1
        while index < len(leaves) and (leaves[index].is_whitespace or leaves[index].ttype in T.Comment):
            index += 1
        return index if index < len(leaves) else None

    def visit(group):
        # Анализируем исходное дерево; правки строковых диапазонов применяются только в конце.
        for child in group.tokens:
            if isinstance(child, Function) and _contains_money(child):
                last = list(child.flatten())[-1]
                index, _, end = positions[id(last)]
                following = next_significant(index)
                if following is not None and leaves[following].normalized == "COLLATE":
                    collation = next_significant(following)
                    if collation is not None and leaves[collation].value.strip('"`[]').lower() != "decimal":
                        _, start, stop = positions[id(leaves[collation])]
                        edits.append((start, stop, "decimal"))
                else:
                    edits.append((end, end, " COLLATE decimal"))
            if isinstance(child, TokenList):
                visit(child)

    for statement in statements:
        visit(statement)
    for start, end, replacement in sorted(edits, reverse=True):
        query = query[:start] + replacement + query[end:]
    return query
