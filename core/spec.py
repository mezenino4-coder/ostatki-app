# -*- coding: utf-8 -*-
"""Проверка наличия деталей по спецификации (по двум складам).

По каждому коду: сначала точное совпадение; если нет — предлагаем варианты
в других «исполнениях» (тот же код до дефиса, напр. 09.040.00.00.03 /
-01 / -02). Списание НЕ выполняем автоматически — только по кнопке «Выдать»
(см. app.py), по отмеченным галочками строкам.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import Store
from .normalize import base_code, first_code_token, normalize_code, parse_qty


@dataclass
class SpecItem:
    code: str
    name: str = ""
    qty_needed: float = 0.0


@dataclass
class TaskLine:
    """Строка задания на сборку — привязка к конкретной строке остатка."""
    spec_code: str
    spec_qty: float          # сколько нужно по спецификации
    stock_id: int = 0        # id строки остатка (для списания)
    code: str = ""           # фактический код (может быть вариантом)
    name: str = ""
    warehouse: str = ""
    cell: str = ""
    available: float = 0.0   # сколько лежит в этой ячейке
    exact: bool = True       # True — точное совпадение, False — вариант
    checked: bool = True


@dataclass
class SpecResult:
    lines: list[TaskLine] = field(default_factory=list)
    not_found: list[SpecItem] = field(default_factory=list)


def parse_spec_text(text: str) -> list[SpecItem]:
    """Разобрать вставленный текст спецификации.

    Формат строки: «код название … количество [треб|плазма|специальная]».
    Количество = ПОСЛЕДНЕЕ число в строке; маркеры игнорируются; код =
    первый токен с цифрой.
    """
    items: list[SpecItem] = []
    markers = {"треб", "плазма", "специальная", "шт", "штук", "штуки"}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t") if "\t" in line else line.split()
        parts = [p for p in parts if p.lower() not in markers]
        if not parts:
            continue
        # количество = последний числовой токен
        qty = 0.0
        idx = len(parts) - 1
        while idx >= 0:
            q = parse_qty(parts[idx])
            if q is not None:
                qty = q
                del parts[idx]
                break
            idx -= 1
        name = " ".join(parts)
        code = normalize_code(first_code_token(name))
        if not code:
            continue
        items.append(SpecItem(code=code, name=name, qty_needed=qty))
    return items


def check_spec(store: Store, spec: list[SpecItem]) -> SpecResult:
    """Проверить наличие по двум складам (без списания)."""
    result = SpecResult()
    for s in spec:
        code = normalize_code(s.code)

        exact = store.stock_by_code(code)
        if exact:
            for r in exact:
                result.lines.append(TaskLine(
                    spec_code=code, spec_qty=s.qty_needed, stock_id=r.id,
                    code=r.code, name=r.name or r.raw or s.name,
                    warehouse=r.warehouse, cell=r.cell, available=r.qty,
                    exact=True))
            continue

        variants = store.stock_by_base(base_code(code))
        if variants:
            for r in variants:
                result.lines.append(TaskLine(
                    spec_code=code, spec_qty=s.qty_needed, stock_id=r.id,
                    code=r.code, name=r.name or r.raw or s.name,
                    warehouse=r.warehouse, cell=r.cell, available=r.qty,
                    exact=False))
            continue

        result.not_found.append(s)

    return result
