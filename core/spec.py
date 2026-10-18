# -*- coding: utf-8 -*-
"""Проверка наличия деталей по спецификации + автосписание с новой базы.

Вход: спецификация (список позиций {code, name, qty_needed}).
Выход: два списка — что лежит на Центральном складе (старая база) и что
на Центральном синем складе (новая база). Списание — автоматическое,
только с НОВОЙ базы, по необходимому количеству.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import OLD_WAREHOUSE, NEW_WAREHOUSE, Store
from .normalize import first_code_token, normalize_code, parse_qty


@dataclass
class SpecItem:
    code: str
    name: str = ""
    qty_needed: float = 0.0


@dataclass
class SpecHit:
    """Найденная позиция на одном из складов."""
    code: str
    name: str
    qty_needed: float
    cell: str = ""
    qty_available: float = 0.0   # остаток (только новая база)
    qty_after: float = 0.0       # остаток после списания (только новая база)


@dataclass
class SpecResult:
    old_hits: list[SpecHit] = field(default_factory=list)  # Центральный склад (старая база)
    new_hits: list[SpecHit] = field(default_factory=list)  # Синий (новая база)


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
    """Проверить наличие и списать (автоматически) с новой базы."""
    result = SpecResult()
    for s in spec:
        code = normalize_code(s.code)

        # старая база (Центральный склад) — только наличие + ячейка
        old = store.old_by_code(code)
        if old is not None:
            result.old_hits.append(SpecHit(
                code=code, name=old.name or s.name, qty_needed=s.qty_needed,
                cell=old.cell,
            ))

        # новая база (синий склад) — наличие + списание (по всем ячейкам)
        nrows = store.new_by_code(code)
        if nrows:
            total = store.new_total(code)
            written = min(total, s.qty_needed)
            after = round(total - written, 6)
            # списываем с ячеек по порядку (id), пока не выберем нужное
            remaining = written
            for r in nrows:
                if remaining <= 0:
                    break
                take = min(r.qty, remaining)
                store.new_set_qty_id(r.id, round(r.qty - take, 6))
                remaining = round(remaining - take, 6)
            cells = ", ".join(sorted({r.cell for r in nrows if r.cell})) or (nrows[0].cell or "")
            result.new_hits.append(SpecHit(
                code=code, name=nrows[0].name or nrows[0].raw or s.name, qty_needed=s.qty_needed,
                cell=cells, qty_available=total, qty_after=after,
            ))

    return result
