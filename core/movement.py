# -*- coding: utf-8 -*-
"""Обработка скринов перемещения: объединение позиций с нескольких страниц."""
from __future__ import annotations


def merge_items(lists):
    """Объединить позиции с нескольких скриншотов (страниц).

    Суммируем количество только для одинаковых (code, cell), пришедших
    с РАЗНЫХ страниц (одна и та же позиция, видимая на двух скринах, или
    строка, разорванная на границе страниц). Внутри одной страницы дубли
    сохраняем как есть.
    """
    result = []
    key_positions: dict[tuple, list[tuple[int, int]]] = {}
    for li, lst in enumerate(lists):
        for it in lst:
            key = (it.code, it.cell or "")
            if key in key_positions:
                merged = False
                for prev_li, idx in key_positions[key]:
                    if prev_li != li:
                        result[idx].qty += it.qty
                        if not result[idx].nf_code:
                            result[idx].nf_code = it.nf_code
                        merged = True
                        break
                if not merged:
                    result.append(it)
                    key_positions[key].append((li, len(result) - 1))
            else:
                result.append(it)
                key_positions[key] = [(li, len(result) - 1)]
    return result
