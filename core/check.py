# -*- coding: utf-8 -*-
"""Проверка целостности базы (диагностика). Только читает, ничего не меняет."""
from __future__ import annotations

from dataclasses import dataclass

from .db import Store


@dataclass
class CheckIssue:
    level: str      # error | warn | info
    table: str      # stock
    code: str
    message: str


def check_store(store: Store) -> list[CheckIssue]:
    issues: list[CheckIssue] = []

    for r in store.stock_all():
        label = r.name or r.raw or "(без названия)"
        if not r.code:
            issues.append(CheckIssue("error", "stock", r.code, f"Пустой код у «{label}»"))
        if r.qty < 0:
            issues.append(CheckIssue("error", "stock", r.code or label,
                                     f"Отрицательный остаток {r.qty} у «{label}»"))
        if not r.warehouse:
            issues.append(CheckIssue("warn", "stock", r.code or label,
                                     f"Не указан склад у «{label}»"))

    return issues
