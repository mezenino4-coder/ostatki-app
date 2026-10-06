# -*- coding: utf-8 -*-
"""Импорт баз остатков из файлов (TSV/CSV).

Старая база (ostatki_po_skladu.tsv):
    Наименование	Код	Ячейка
    09.051.05.00.01-02 Фланец крышки нижний	НФ-00036515	С-5-3-3-2
  - код для матчинга = первый токен «Наименования» (содержит цифру);
  - «Код» (НФ-…) — внутренний, храним в raw/name для справки.

Новая база — то же + количество (колонка «Остаток»/«Количество»).
"""
from __future__ import annotations

import csv
import re

from .db import NewRow, OldRow, Store
from .normalize import normalize_code


def _first_code_token(name: str) -> str:
    """Первый токен наименования, содержащий цифру — это код."""
    for tok in (name or "").split():
        if re.search(r"\d", tok):
            return tok
    return (name or "").split()[0] if (name or "").strip() else ""


def _split_name(name: str) -> tuple[str, str]:
    """Разбить «код Название» -> (код, название)."""
    name = (name or "").strip()
    parts = name.split(" ", 1)
    code = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    return code, rest


def split_full_name(raw_text: str) -> tuple[str, str]:
    """Полное наименование «код Название» -> (код_детали, название)."""
    code_raw, disp_name = _split_name(raw_text)
    return normalize_code(code_raw), disp_name


def load_old_tsv(store: Store, path: str, clear: bool = True):
    """Загрузить старую базу из TSV (Наименование, Код, Ячейка)."""
    rows: list[OldRow] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        # определить индексы колонок по заголовку
        cols = {h.strip().lower(): i for i, h in enumerate(header or [])}

        def idx(*names):
            for n in names:
                if n in cols:
                    return cols[n]
            return None

        i_name = idx("наименование", "name")
        i_cell = idx("ячейка", "cell")
        i_nf = idx("код", "нф", "nf_code", "унф")
        for line in reader:
            if len(line) < 2:
                continue
            name = line[i_name] if i_name is not None and i_name < len(line) else line[0]
            cell = line[i_cell] if i_cell is not None and i_cell < len(line) else (line[2] if len(line) > 2 else "")
            nf_code = line[i_nf] if i_nf is not None and i_nf < len(line) else ""
            code_raw, disp_name = _split_name(name)
            code = normalize_code(code_raw)
            if not code:
                continue
            rows.append(OldRow(code=code, raw=name, name=disp_name,
                               cell=cell.strip(), nf_code=nf_code.strip()))
    store.load_old(rows, clear=clear)
    return len(rows)


def load_new_tsv(store: Store, path: str, clear: bool = True):
    """Загрузить новую базу из TSV (Наименование, Код, Ячейка, Остаток)."""
    rows: list[NewRow] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        cols = {h.strip().lower(): i for i, h in enumerate(header or [])}

        def idx(*names):
            for n in names:
                if n in cols:
                    return cols[n]
            return None

        i_name = idx("наименование", "name")
        i_cell = idx("ячейка", "cell")
        i_qty = idx("остаток", "количество", "qty", "кол-во")
        for line in reader:
            if len(line) < 2:
                continue
            name = line[i_name] if i_name is not None and i_name < len(line) else line[0]
            cell = line[i_cell] if i_cell is not None and i_cell < len(line) else (line[2] if len(line) > 2 else "")
            qty = 0.0
            if i_qty is not None and i_qty < len(line):
                try:
                    qty = float(str(line[i_qty]).replace(",", ".").replace(" ", ""))
                except ValueError:
                    qty = 0.0
            code_raw, disp_name = _split_name(name)
            code = normalize_code(code_raw)
            if not code:
                continue
            rows.append(NewRow(code=code, raw=name, name=disp_name, cell=cell.strip(), qty=qty))
    store.load_new(rows, clear=clear)
    return len(rows)
