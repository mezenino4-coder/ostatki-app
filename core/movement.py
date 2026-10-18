# -*- coding: utf-8 -*-
"""Обработка документа «Перемещение» (пополнение нового склада).

После OCR скрина каждая позиция несёт: код детали, НФ-код, количество,
ячейку (построчно). Склад-отправитель — ОДИН на весь документ, берётся
из шапки «Склад: …».

Источник документа (source):
  - "shelf"   — отправитель «Склад Полка»: просто наполняем новую базу;
  - "central" — «Центральный склад» (старая база): приход в новую + списание
    ячеек из старой (со сверкой «полностью ли перенесли»);
  - ""        — склад не распознан: по умолчанию просто наполняем новую базу.

Два этапа:
  1. analyze_movement — только сверка ячеек, БЕЗ записи в базу;
  2. apply_movement   — физическая запись (списание старого + приход в новое).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import Store


@dataclass
class MoveItem:
    code: str      # нормализованный код детали
    raw: str       # исходная строка наименования
    qty: float
    nf_code: str = ""   # НФ-код (отдельная колонка «Код»)
    cell: str = ""      # ячейка строки


@dataclass
class CellCheck:
    """Сверка одной ячейки Центрального склада (для source=central)."""
    cell: str
    missing_in_move: list[str] = field(default_factory=list)  # были в ячейке, нет в перемещении
    extra_in_move: list[str] = field(default_factory=list)    # в перемещении, не было в ячейке

    @property
    def ok(self) -> bool:
        return not self.missing_in_move and not self.extra_in_move


@dataclass
class MoveResult:
    items: list[MoveItem] = field(default_factory=list)
    source: str = ""                             # "shelf" | "central" | ""
    cell_checks: list[CellCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.cell_checks)


def merge_items(lists: list[list[MoveItem]]) -> list[MoveItem]:
    """Объединить позиции с нескольких скриншотов (страниц).

    Внутри одной страницы каждая строка — отдельная позиция (дубли в документе
    сохраняем). Суммируем количество только для одинаковых (код, ячейка),
    пришедших с РАЗНЫХ страниц — например, строка, разорванная на границе
    страниц, или одна и та же позиция, видимая на двух скринах.
    """
    result: list[MoveItem] = []
    # (code, cell) -> [(page_index, result_index)]
    key_positions: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for li, lst in enumerate(lists):
        for it in lst:
            key = (it.code, it.cell or "")
            if key in key_positions:
                # ищем такую же позицию с ДРУГОЙ страницы
                merged = False
                for prev_li, idx in key_positions[key]:
                    if prev_li != li:
                        result[idx].qty += it.qty
                        if not result[idx].nf_code:
                            result[idx].nf_code = it.nf_code
                        merged = True
                        break
                if not merged:
                    # ключ был только на этой же странице — отдельная строка
                    result.append(it)
                    key_positions[key].append((li, len(result) - 1))
            else:
                result.append(it)
                key_positions[key] = [(li, len(result) - 1)]
    return result


def analyze_movement(store: Store, items: list[MoveItem],
                     source: str = "") -> MoveResult:
    """Сверка ячеек Центрального склада (без записи в базу).

    source — склад-отправитель документа (из шапки). При source == "central"
    сверяем каждую ячейку перемещения со старой базой: что в ячейке осталось,
    что лишнее. При source == "shelf"/"" — без сверки (просто приход).
    """
    result = MoveResult(items=items, source=source)

    if source == "central":
        by_cell: dict[str, list[MoveItem]] = {}
        for it in items:
            if it.cell:
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


def apply_movement(store: Store, result: MoveResult, confirm_force: bool = False,
                   cell_map: dict[str, str] | None = None):
    """Физически провести перемещение.

    - source == "central": для каждой сверенной ячейки — если состав совпал
      1-в-1 ИЛИ подтверждено «провести всё равно» — удаляем ячейку из старой
      базы; при расхождении без подтверждения эту ячейку не списываем.
    - приход в новую базу — всегда (для всех позиций).

    cell_map — решение пользователя по деталям, которые уже есть на складе:
    код -> итоговая ячейка. Если кода нет в карте, используется ячейка из
    документа (it.cell), а если деталь уже лежит в этой ячейке — количество
    прибавляется туда, иначе создаётся строка в новой ячейке.
    """
    cell_map = cell_map or {}
    if result.source == "central":
        for c in result.cell_checks:
            if c.ok or confirm_force:
                store.delete_old_cell(c.cell)

    for it in result.items:
        cell = cell_map.get(it.code, it.cell)
        store.new_upsert(it.code, it.raw, "", cell, it.qty, it.nf_code)
    return True
