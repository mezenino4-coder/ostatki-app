# -*- coding: utf-8 -*-
"""Локальная БД остатков (SQLite).

Две таблицы:
  - old: старая база (Центральный склад). Код уникален — одна деталь = одна ячейка.
    Наименование + Код + Ячейка, БЕЗ количества.
  - new: новая база (синий склад). Код уникален — одна деталь лежит в ОДНОЙ
    ячейке. Если деталь приходит повторно, она добавляется к уже существующей
    записи (в её же ячейку), а не создаётся во второй ячейке.
    Код + Наименование + Ячейка + Количество.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ostatki.db")

# Имена складов (для отображения и печати)
OLD_WAREHOUSE = "Центральный склад"          # старая база (уменьшается, не пополняется)
NEW_WAREHOUSE = "Центральный синий склад"  # новая база (пополняется перемещениями)


@dataclass
class OldRow:
    code: str          # код детали (для матчинга, норм.)
    raw: str           # полное наименование (код + название)
    name: str          # название (человекочитаемое)
    cell: str          # ячейка
    nf_code: str = ""  # НФ-код (внутренний код 1С)


@dataclass
class NewRow:
    code: str
    raw: str
    name: str
    cell: str
    qty: float
    nf_code: str = ""


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or DEFAULT_DB
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS old (
                code TEXT PRIMARY KEY,
                raw  TEXT,
                name TEXT,
                cell TEXT,
                nf_code TEXT
            );
            CREATE TABLE IF NOT EXISTS new (
                code TEXT PRIMARY KEY,
                raw  TEXT,
                name TEXT,
                cell TEXT,
                qty  REAL NOT NULL DEFAULT 0,
                nf_code TEXT
            );
            """
        )
        # миграция: добавить колонку nf_code, если её ещё нет (старые БД)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(old)").fetchall()]
        if "nf_code" not in cols:
            self.conn.execute("ALTER TABLE old ADD COLUMN nf_code TEXT")
        ncols = [r[1] for r in self.conn.execute("PRAGMA table_info(new)").fetchall()]
        if "nf_code" not in ncols:
            self.conn.execute("ALTER TABLE new ADD COLUMN nf_code TEXT")
        self.conn.commit()

    # ---------- старая база (Центральный склад) ----------
    def load_old(self, rows: list[OldRow], clear: bool = True):
        if clear:
            self.conn.execute("DELETE FROM old")
        self.conn.executemany(
            "INSERT OR REPLACE INTO old(code, raw, name, cell, nf_code) VALUES (?,?,?,?,?)",
            [(r.code, r.raw, r.name, r.cell, r.nf_code) for r in rows],
        )
        self.conn.commit()

    def old_by_cell(self, cell: str) -> list[OldRow]:
        cur = self.conn.execute(
            "SELECT code, raw, name, cell, nf_code FROM old WHERE cell = ? ORDER BY code", (cell,)
        )
        return [OldRow(*r) for r in cur.fetchall()]

    def old_by_code(self, code: str) -> OldRow | None:
        cur = self.conn.execute(
            "SELECT code, raw, name, cell, nf_code FROM old WHERE code = ?", (code,)
        )
        r = cur.fetchone()
        return OldRow(*r) if r else None

    def old_all(self) -> list[OldRow]:
        cur = self.conn.execute("SELECT code, raw, name, cell, nf_code FROM old ORDER BY cell, code")
        return [OldRow(*r) for r in cur.fetchall()]

    def delete_old_cell(self, cell: str):
        self.conn.execute("DELETE FROM old WHERE cell = ?", (cell,))
        self.conn.commit()

    def old_update(self, code: str, raw: str, name: str, cell: str, nf_code: str = ""):
        self.conn.execute(
            "UPDATE old SET raw=?, name=?, cell=?, nf_code=? WHERE code=?",
            (raw, name, cell, nf_code, code),
        )
        self.conn.commit()

    def old_upsert(self, code: str, raw: str, name: str, cell: str, nf_code: str = ""):
        self.conn.execute(
            "INSERT INTO old(code, raw, name, cell, nf_code) VALUES (?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET raw=excluded.raw, name=excluded.name, "
            "cell=excluded.cell, nf_code=excluded.nf_code",
            (code, raw, name, cell, nf_code),
        )
        self.conn.commit()

    def old_delete(self, code: str):
        self.conn.execute("DELETE FROM old WHERE code = ?", (code,))
        self.conn.commit()

    # ---------- новая база (синий склад) ----------
    def load_new(self, rows: list[NewRow], clear: bool = True):
        if clear:
            self.conn.execute("DELETE FROM new")
        self.conn.executemany(
            "INSERT OR REPLACE INTO new(code, raw, name, cell, qty, nf_code) VALUES (?,?,?,?,?,?)",
            [(r.code, r.raw, r.name, r.cell, r.qty, r.nf_code) for r in rows],
        )
        self.conn.commit()

    def new_get(self, code: str) -> NewRow | None:
        cur = self.conn.execute(
            "SELECT code, raw, name, cell, qty, nf_code FROM new WHERE code = ?", (code,)
        )
        r = cur.fetchone()
        return NewRow(*r) if r else None

    def new_all(self) -> list[NewRow]:
        cur = self.conn.execute("SELECT code, raw, name, cell, qty, nf_code FROM new ORDER BY code")
        return [NewRow(*r) for r in cur.fetchall()]

    def new_upsert(self, code: str, raw: str, name: str, cell: str, qty: float, nf_code: str = ""):
        """Добавить количество. Если деталь уже есть в новой базе — прибавляем
        к существующей записи (в её же ячейку), НЕ создаём вторую ячейку."""
        ex = self.new_get(code)
        if ex is None:
            self.conn.execute(
                "INSERT INTO new(code, raw, name, cell, qty, nf_code) VALUES (?,?,?,?,?,?)",
                (code, raw, name, cell, qty, nf_code),
            )
        else:
            self.conn.execute(
                "UPDATE new SET qty = qty + ?, "
                "raw = COALESCE(NULLIF(?, ''), raw), name = COALESCE(NULLIF(?, ''), name) "
                "WHERE code = ?",
                (qty, raw, name, code),
            )
        self.conn.commit()

    def new_upsert_full(self, code: str, raw: str, name: str, cell: str, qty: float, nf_code: str = ""):
        """Вставить/заменить строку, количество — как есть."""
        self.conn.execute(
            "INSERT INTO new(code, raw, name, cell, qty, nf_code) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET raw=excluded.raw, name=excluded.name, "
            "cell=excluded.cell, qty=excluded.qty, nf_code=excluded.nf_code",
            (code, raw, name, cell, qty, nf_code),
        )
        self.conn.commit()

    def new_set_qty(self, code: str, qty: float):
        self.conn.execute("UPDATE new SET qty = ? WHERE code = ?", (qty, code))
        self.conn.commit()

    def new_delete(self, code: str):
        self.conn.execute("DELETE FROM new WHERE code = ?", (code,))
        self.conn.commit()

    def new_cells(self) -> list[str]:
        """Список уникальных ячеек новой базы."""
        cur = self.conn.execute(
            "SELECT DISTINCT cell FROM new WHERE cell IS NOT NULL AND cell != '' ORDER BY cell"
        )
        return [r[0] for r in cur.fetchall()]

    def move_cell(self, old_cell: str, new_cell: str) -> int:
        """Перенос целой ячейки (все позиции поддона) в другую ячейку.

        Коды уникальны по всей базе, поэтому конфликтов быть не может —
        просто меняем ячейку у всех строк. Возвращает число позиций.
        """
        cur = self.conn.execute(
            "UPDATE new SET cell = ? WHERE cell = ?", (new_cell, old_cell)
        )
        self.conn.commit()
        return cur.rowcount

    def merge_cells(self, cell_a: str, cell_b: str, target_cell: str) -> int:
        """Слить две ячейки в одну: все детали из обеих переезжают в target_cell.

        Коды уникальны, поэтому просто переносим обе ячейки в одну.
        Возвращает число позиций.
        """
        cur = self.conn.execute(
            "UPDATE new SET cell = ? WHERE cell IN (?,?)", (target_cell, cell_a, cell_b)
        )
        self.conn.commit()
        return cur.rowcount

    # ---------- сервис: счётчики и поиск ----------
    def old_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM old").fetchone()[0]

    def new_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM new").fetchone()[0]

    def old_search(self, q: str) -> list[OldRow]:
        like = f"%{q}%"
        cur = self.conn.execute(
            "SELECT code, raw, name, cell, nf_code FROM old "
            "WHERE code LIKE ? OR name LIKE ? OR raw LIKE ? OR cell LIKE ? OR nf_code LIKE ? "
            "ORDER BY cell, code",
            (like, like, like, like, like),
        )
        return [OldRow(*r) for r in cur.fetchall()]

    def new_search(self, q: str) -> list[NewRow]:
        like = f"%{q}%"
        cur = self.conn.execute(
            "SELECT code, raw, name, cell, qty, nf_code FROM new "
            "WHERE code LIKE ? OR name LIKE ? OR raw LIKE ? OR cell LIKE ? OR nf_code LIKE ? ORDER BY code",
            (like, like, like, like, like),
        )
        return [NewRow(*r) for r in cur.fetchall()]

    def close(self):
        self.conn.close()
