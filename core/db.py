# -*- coding: utf-8 -*-
"""Локальная БД остатков (SQLite).

Две таблицы:
  - old: старая база (Центральный склад). Код уникален — одна деталь = одна ячейка.
    Наименование + Код + Ячейка, БЕЗ количества.
  - new: новая база (синий склад). Одна и та же деталь (код) МОЖЕТ лежать в
    нескольких ячейках — поэтому первичный ключ это автоинкрементный id, а не код.
    При повторном приходе деталь можно положить в уже занятую ею ячейку
    (прибавить количество) или в новую ячейку (создать отдельную строку).
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
    id: int = 0        # автоинкрементный id (первичный ключ)
    code: str = ""
    raw: str = ""
    name: str = ""
    cell: str = ""
    qty: float = 0.0
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
            """
        )
        # миграция: добавить колонку nf_code, если её ещё нет (старые БД)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(old)").fetchall()]
        if "nf_code" not in cols:
            self.conn.execute("ALTER TABLE old ADD COLUMN nf_code TEXT")

        # новая база: id — первичный ключ, код может повторяться (несколько ячеек)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS new (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                raw  TEXT,
                name TEXT,
                cell TEXT,
                qty  REAL NOT NULL DEFAULT 0,
                nf_code TEXT
            )
            """
        )
        ncols = [r[1] for r in self.conn.execute("PRAGMA table_info(new)").fetchall()]
        if "id" not in ncols:
            # старая схема (code был PRIMARY KEY) — пересоздаём с id
            self._migrate_new()
        else:
            if "nf_code" not in ncols:
                self.conn.execute("ALTER TABLE new ADD COLUMN nf_code TEXT")
        self.conn.commit()

    def _migrate_new(self):
        """Миграция старой схемы new (code PK) на новую (id PK)."""
        self.conn.execute("ALTER TABLE new RENAME TO new_legacy")
        self.conn.execute(
            """
            CREATE TABLE new (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                raw  TEXT,
                name TEXT,
                cell TEXT,
                qty  REAL NOT NULL DEFAULT 0,
                nf_code TEXT
            )
            """
        )
        lcols = [r[1] for r in self.conn.execute("PRAGMA table_info(new_legacy)").fetchall()]
        nf = "nf_code" if "nf_code" in lcols else "NULL"
        self.conn.execute(
            f"INSERT INTO new(code, raw, name, cell, qty, nf_code) "
            f"SELECT code, raw, name, cell, qty, {nf} FROM new_legacy"
        )
        self.conn.execute("DROP TABLE new_legacy")

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
            "INSERT INTO new(code, raw, name, cell, qty, nf_code) VALUES (?,?,?,?,?,?)",
            [(r.code, r.raw, r.name, r.cell, r.qty, r.nf_code) for r in rows],
        )
        self.conn.commit()

    def new_get_id(self, row_id: int) -> NewRow | None:
        cur = self.conn.execute(
            "SELECT id, code, raw, name, cell, qty, nf_code FROM new WHERE id = ?", (row_id,)
        )
        r = cur.fetchone()
        return NewRow(*r) if r else None

    def new_by_code(self, code: str) -> list[NewRow]:
        """Все строки с данным кодом (деталь может быть в нескольких ячейках)."""
        cur = self.conn.execute(
            "SELECT id, code, raw, name, cell, qty, nf_code FROM new WHERE code = ? ORDER BY id", (code,)
        )
        return [NewRow(*r) for r in cur.fetchall()]

    def new_cells_for(self, code: str) -> list[str]:
        """Уникальные непустые ячейки, в которых лежит данная деталь."""
        cur = self.conn.execute(
            "SELECT DISTINCT cell FROM new WHERE code = ? AND cell IS NOT NULL AND cell != '' "
            "ORDER BY cell", (code,)
        )
        return [r[0] for r in cur.fetchall()]

    def new_total(self, code: str) -> float:
        """Суммарный остаток детали по всем ячейкам."""
        r = self.conn.execute(
            "SELECT COALESCE(SUM(qty), 0) FROM new WHERE code = ?", (code,)
        ).fetchone()
        return float(r[0])

    def new_all(self) -> list[NewRow]:
        cur = self.conn.execute(
            "SELECT id, code, raw, name, cell, qty, nf_code FROM new ORDER BY code, cell, id"
        )
        return [NewRow(*r) for r in cur.fetchall()]

    def new_upsert(self, code: str, raw: str, name: str, cell: str, qty: float, nf_code: str = ""):
        """Добавить количество в конкретную ячейку.

        Если деталь с таким кодом УЖЕ лежит в этой ячейке — прибавляем количество.
        Иначе создаём новую строку (деталь в новой ячейке).
        """
        cell = cell or ""
        cur = self.conn.execute(
            "SELECT id FROM new WHERE code = ? AND cell = ? ORDER BY id LIMIT 1", (code, cell)
        )
        r = cur.fetchone()
        if r is None:
            self.conn.execute(
                "INSERT INTO new(code, raw, name, cell, qty, nf_code) VALUES (?,?,?,?,?,?)",
                (code, raw, name, cell, qty, nf_code),
            )
        else:
            self.conn.execute(
                "UPDATE new SET qty = qty + ?, "
                "raw = COALESCE(NULLIF(?, ''), raw), name = COALESCE(NULLIF(?, ''), name) "
                "WHERE id = ?",
                (qty, raw, name, r[0]),
            )
        self.conn.commit()

    def new_insert(self, code: str, raw: str, name: str, cell: str, qty: float, nf_code: str = "") -> int:
        """Вставить новую строку. Возвращает id."""
        cur = self.conn.execute(
            "INSERT INTO new(code, raw, name, cell, qty, nf_code) VALUES (?,?,?,?,?,?)",
            (code, raw, name, cell, qty, nf_code),
        )
        self.conn.commit()
        return cur.lastrowid

    def new_update(self, row_id: int, code: str, raw: str, name: str, cell: str, qty: float, nf_code: str = ""):
        self.conn.execute(
            "UPDATE new SET code=?, raw=?, name=?, cell=?, qty=?, nf_code=? WHERE id=?",
            (code, raw, name, cell, qty, nf_code, row_id),
        )
        self.conn.commit()

    def new_set_qty_id(self, row_id: int, qty: float):
        self.conn.execute("UPDATE new SET qty = ? WHERE id = ?", (qty, row_id))
        self.conn.commit()

    def new_delete_id(self, row_id: int):
        self.conn.execute("DELETE FROM new WHERE id = ?", (row_id,))
        self.conn.commit()

    def new_cells(self) -> list[str]:
        """Список уникальных ячеек новой базы."""
        cur = self.conn.execute(
            "SELECT DISTINCT cell FROM new WHERE cell IS NOT NULL AND cell != '' ORDER BY cell"
        )
        return [r[0] for r in cur.fetchall()]

    def move_cell(self, old_cell: str, new_cell: str) -> int:
        """Перенос целой ячейки (все позиции поддона) в другую ячейку.

        Просто меняем ячейку у всех строк. Возвращает число позиций.
        """
        cur = self.conn.execute(
            "UPDATE new SET cell = ? WHERE cell = ?", (new_cell, old_cell)
        )
        self.conn.commit()
        return cur.rowcount

    def merge_cells(self, cell_a: str, cell_b: str, target_cell: str) -> int:
        """Слить две ячейки в одну: все детали из обеих переезжают в target_cell.

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
            "SELECT id, code, raw, name, cell, qty, nf_code FROM new "
            "WHERE code LIKE ? OR name LIKE ? OR raw LIKE ? OR cell LIKE ? OR nf_code LIKE ? "
            "ORDER BY code, cell, id",
            (like, like, like, like, like),
        )
        return [NewRow(*r) for r in cur.fetchall()]

    def close(self):
        self.conn.close()
