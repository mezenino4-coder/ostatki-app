# -*- coding: utf-8 -*-
"""Обработка документа «Перемещение» (пополнение нового склада).

После OCR скрина каждая позиция несёт: код детали, НФ-код, количество,
ячейку и склад-источник (построчно).

Источник строки (source):
  - "shelf"   — «Склад Полка»: просто наполняем новую базу;
  - "central" — «Центральный склад» (старая база): приход в новую + списание
    ячейки из старой (со сверкой «полностью ли перенесли»);
  - ""        — склад не распознан: по умолчанию просто наполняем новую базу
    (не трогаем старую).

Два этапа:
  1. analyze_movement — только сверка ячеек, БЕЗ записи в базу;
  2. apply_movement   — физическая запись (списание старого + приход в новое).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import Store
from .normalize import normalize_code


@dataclass
class MoveItem:
    code: str      # нормализованный код детали
    raw: str       # исходная строка наименования
    qty: float
    nf_code: str = ""   # НФ-код (отдельная колонка «Код»)
    cell: str = ""      # ячейка строки
    source: str = ""    # "shelf" | "central" | ""


@dataclass
class CellCheck:
    """Сверка одной ячейки Центрального склада (для строк source=central)."""
    cell: str
    missing_in_move: list[str] = field(default_factory=list)  # были в ячейке, нет в перемещении
    extra_in_move: list[str] = field(default_factory=list)    # в перемещении, не было в ячейке

    @property
    def ok(self) -> bool:
        return not self.missing_in_move and not self.extra_in_move


@dataclass
class MoveResult:
    items: list[MoveItem] = field(default_factory=list)
    cell_checks: list[CellCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.cell_checks)


def merge_items(lists: list[list[MoveItem]]) -> list[MoveItem]:
    """Объединить позиции с нескольких скриншотов.

    Суммируем количество по паре (код, ячейка) — одна деталь в одной ячейке.
    """
    seen: dict[tuple[str, str], MoveItem] = {}
    for lst in lists:
        for it in lst:
            key = (it.code, it.cell or "")
            if key in seen:
                seen[key].qty += it.qty
                if not seen[key].nf_code:
                    seen[key].nf_code = it.nf_code
            else:
                seen[key] = it
    return list(seen.values())


def analyze_movement(store: Store, items: list[MoveItem]) -> MoveResult:
    """Сверка ячеек Центрального склада (без записи в базу).

    Для каждой строки с source == "central" сверяем состав её ячейки в старой
    базе с фактическим составом перемещения. Строки source == "shelf"/"" —
    без сверки (просто приход в новую базу).
    """
    result = MoveResult(items=items)

    by_cell: dict[str, list[MoveItem]] = {}
    for it in items:
        if it.source == "central" and it.cell:
            by_cell.setdefault(it.cell, []).append(it)

    for cell in sorted(by_cell):
        cell_items = by_cell[cell]
        old_codes = {r.code for r in store.old_by_cell(cell)}
        move_codes = {i.code for i in cell_items}
        result.cell_checks.append(CellCheck(
            cell=cell,
            missing_in_move=sorted(old_codes - move_codes),
            extra_in_move=sorted(move_codes - old_codes),
        ))
    return result


def apply_movement(store: Store, result: MoveResult, confirm_force: bool = False):
    """Физически провести перемещение.

    - для каждой сверенной ячейки центрального склада: если состав совпал
      1-в-1 ИЛИ подтверждено «провести всё равно» (confirm_force=True) —
      удаляем ячейку из старой базы; при расхождении без подтверждения
      эту ячейку не списываем.
    - приход в новую базу — всегда (для всех позиций).
    """
    # списание со старого (только central)
    for c in result.cell_checks:
        if c.ok or confirm_force:
            store.delete_old_cell(c.cell)

    # приход в новую базу
    for it in result.items:
        store.new_upsert(it.code, it.raw, "", it.cell, it.qty, it.nf_code)
    return True
