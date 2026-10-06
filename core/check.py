# -*- coding: utf-8 -*-
"""Проверка целостности базы (диагностика).

Только читает, ничего не меняет. Возвращает список проблем для просмотра.
"""
from __future__ import annotations

from dataclasses import dataclass

from .db import Store


@dataclass
class CheckIssue:
    level: str   # error | warn | info
    table: str   # old | new
    code: str
    message: str


def check_store(store: Store) -> list[CheckIssue]:
    issues: list[CheckIssue] = []

    for r in store.old_all():
        label = r.name or r.raw or "(без названия)"
        if not r.code:
            issues.append(CheckIssue("error", "old", r.code, f"Пустой код у «{label}»"))
        if not r.cell:
            issues.append(CheckIssue("warn", "old", r.code or label, f"Пустая ячейка у «{label}»"))

    for r in store.new_all():
        label = r.name or r.raw or "(без названия)"
        if not r.code:
            issues.append(CheckIssue("error", "new", r.code, f"Пустой код у «{label}»"))
        if r.qty < 0:
            issues.append(CheckIssue("error", "new", r.code or label, f"Отрицательный остаток {r.qty} у «{label}»"))
        if not r.name and not r.raw:
            issues.append(CheckIssue("warn", "new", r.code or label, "Нет названия (OCR не распознал номенклатуру)"))

    return issues
