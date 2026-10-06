# -*- coding: utf-8 -*-
"""Сверка новой базы программы с выгрузкой 1С УНФ.

Вход: вставленная таблица из 3 колонок (Наименование | УНФ-код | Ячейка),
как в старой базе остатков. Матчинг — по коду детали (первый токен
наименования, содержащий цифру). Сверяем: лишние в 1С, лишние у нас,
расхождение ячейки.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .db import NewRow, Store
from .normalize import first_code_token, normalize_code


@dataclass
class OneCRow:
    name: str        # наименование (код + название)
    unf_code: str    # внутренний код УНФ (НФ-…)
    cell: str        # ячейка
    code: str        # нормализованный код детали


@dataclass
class CompareResult:
    only_in_1c: list[OneCRow] = field(default_factory=list)   # есть в 1С, нет в нашей базе
    only_in_db: list[NewRow] = field(default_factory=list)    # есть у нас, нет в 1С
    cell_mismatch: list[tuple[OneCRow, str]] = field(default_factory=list)  # (1С-строка, наши ячейки)
    matched: int = 0


_CELL_RE = re.compile(r"[СC]-?\d+", re.IGNORECASE)
_UNF_RE = re.compile(r"Н[ФF][-–—]?", re.IGNORECASE)


def parse_1c_table(text: str) -> list[OneCRow]:
    """Разобрать вставленную таблицу 1С (3 колонки)."""
    rows: list[OneCRow] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
            name, unf, cell = (parts + ["", "", ""])[:3]
        else:
            toks = line.split()
            cell = next((t for t in toks if _CELL_RE.match(t)), "")
            unf = next((t for t in toks if _UNF_RE.search(t)), "")
            name = " ".join(t for t in toks if t not in (cell, unf))
        if not name:
            continue
        code = normalize_code(first_code_token(name))
        if not code:
            continue
        rows.append(OneCRow(name=name, unf_code=unf, cell=cell, code=code))
    return rows


def _norm_cell(s: str) -> str:
    return normalize_code(s or "")


def compare_1c(store: Store, rows: list[OneCRow]) -> CompareResult:
    """Сверить новую базу с выгрузкой 1С (по коду детали и ячейке)."""
    db_cells: dict[str, set[str]] = {}
    for r in store.new_all():
        db_cells.setdefault(r.code, set()).add(r.cell)

    result = CompareResult()

    for r in rows:
        cells = db_cells.get(r.code)
        if cells is None:
            result.only_in_1c.append(r)
        elif r.cell and r.cell not in cells:
            result.cell_mismatch.append((r, ", ".join(sorted(cells))))
        else:
            result.matched += 1

    codes_1c = {r.code for r in rows}
    for r in store.new_all():
        if r.code not in codes_1c:
            result.only_in_db.append(r)

    return result
