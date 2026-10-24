# -*- coding: utf-8 -*-
"""Локальная БД остатков (SQLite).

Два склада, оба пополняются через «Перемещения»:
  - Центральный синий
  - Бегоулево

Таблицы:
  - stock          — остатки по складам (code, raw, name, cell, qty, nf_code, warehouse);
  - movements      — перемещения (draft -> posted), хранятся в базе;
  - movement_items — строки перемещения.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime

# --- склады ---
BLUE = "Центральный синий"
BEGOULEVO = "Бегоулево"
WAREHOUSES = [BLUE, BEGOULEVO]

# --- статусы перемещения ---
DRAFT = "draft"
POSTED = "posted"


def match_warehouse(text: str) -> str:
    """Определить склад по тексту из шапки «Склад: …». Возвращает имя склада или ''."""
    t = (text or "").lower()
    if "бегоул" in t or "бегаул" in t:
        return BEGOULEVO
    if "синий" in t:
        return BLUE
    return ""


def _data_dir() -> str:
    """Папка с данными: рядом с exe (frozen) или корень проекта (dev)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bundled_db_path() -> str | None:
    """Старый (ошибочный) путь к базе внутри пакета — для одноразовой миграции."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    return os.path.join(meipass, "ostatki.db")


DEFAULT_DB = os.path.join(_data_dir(), "ostatki.db")


def _migrate_db_if_needed(path: str) -> None:
    """Одноразовый перенос базы из _internal/ (старые сборки) рядом с exe."""
    if os.path.exists(path):
        return
    bundled = _bundled_db_path()
    if not bundled or not os.path.exists(bundled):
        return
    try:
        shutil.copy2(bundled, path)
        for suf in ("-wal", "-shm"):
            s = bundled + suf
            if os.path.exists(s):
                shutil.copy2(s, path + suf)
    except Exception:
        pass


@dataclass
class StockRow:
    id: int = 0
    code: str = ""
    raw: str = ""
    name: str = ""
    cell: str = ""
    qty: float = 0.0
    nf_code: str = ""
    warehouse: str = ""


@dataclass
class MovementItem:
    id: int = 0          # id в БД (для строки движения)
    row_num: int = 0
    nf_code: str = ""
    raw: str = ""
    code: str = ""
    name: str = ""
    cell: str = ""
    qty: float = 0.0


@dataclass
class Movement:
    id: int = 0
    created_at: str = ""
    warehouse: str = ""
    status: str = DRAFT
    items: list[MovementItem] = field(default_factory=list)

    @property
    def total(self) -> float:
        """Итого — суммарное количество единиц деталей в перемещении."""
        return sum(it.qty for it in self.items)

    @property
    def count(self) -> int:
        return len(self.items)


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or DEFAULT_DB
        if path is None:
            _migrate_db_if_needed(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                raw TEXT,
                name TEXT,
                cell TEXT,
                qty REAL NOT NULL DEFAULT 0,
                nf_code TEXT,
                warehouse TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                warehouse TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft'
            );
            CREATE TABLE IF NOT EXISTS movement_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movement_id INTEGER NOT NULL,
                row_num INTEGER,
                nf_code TEXT,
                raw TEXT,
                code TEXT,
                name TEXT,
                cell TEXT,
                qty REAL NOT NULL DEFAULT 0
            );
            """
        )
        self._migrate_legacy()
        self.conn.commit()

    def _migrate_legacy(self):
        """Миграция старой схемы (old/new) на новую (stock с warehouse)."""
        # stock: добавить warehouse, если колонки ещё нет
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(stock)").fetchall()]
        if "warehouse" not in cols:
            self.conn.execute("ALTER TABLE stock ADD COLUMN warehouse TEXT NOT NULL DEFAULT ''")

        # перенос старой таблицы "new" -> stock (как «Центральный синий»)
        try:
            has_new = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='new'").fetchone()
            if has_new:
                cnt = self.conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
                if cnt == 0:
                    self.conn.execute(
                        "INSERT INTO stock(code, raw, name, cell, qty, nf_code, warehouse) "
                        "SELECT code, raw, name, cell, qty, nf_code, ? FROM new", (BLUE,))
                self.conn.execute("DROP TABLE new")
        except Exception:
            pass

        # старую таблицу "old" убираем совсем (Центральный склад больше не нужен)
        try:
            has_old = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='old'").fetchone()
            if has_old:
                self.conn.execute("DROP TABLE old")
        except Exception:
            pass

    # ==================================================================
    #  Остатки (stock)
    # ==================================================================
    def stock_upsert(self, code, raw, name, cell, qty, nf_code="", warehouse=""):
        """Приход: прибавить количество в (code, cell, warehouse)."""
        cell = cell or ""
        warehouse = warehouse or ""
        cur = self.conn.execute(
            "SELECT id FROM stock WHERE code=? AND cell=? AND warehouse=? ORDER BY id LIMIT 1",
            (code, cell, warehouse))
        r = cur.fetchone()
        if r is None:
            self.conn.execute(
                "INSERT INTO stock(code, raw, name, cell, qty, nf_code, warehouse) "
                "VALUES (?,?,?,?,?,?,?)",
                (code, raw, name, cell, qty, nf_code, warehouse))
        else:
            self.conn.execute(
                "UPDATE stock SET qty=qty+?, "
                "raw=COALESCE(NULLIF(?, ''), raw), name=COALESCE(NULLIF(?, ''), name) "
                "WHERE id=?",
                (qty, raw, name, r[0]))
        self.conn.commit()

    def stock_write_off_id(self, row_id, qty):
        """Списать количество с конкретной строки остатка (по id)."""
        r = self.conn.execute("SELECT qty FROM stock WHERE id=?", (row_id,)).fetchone()
        if r is None:
            return
        new_qty = r[0] - qty
        if new_qty <= 0:
            self.conn.execute("DELETE FROM stock WHERE id=?", (row_id,))
        else:
            self.conn.execute("UPDATE stock SET qty=? WHERE id=?", (new_qty, row_id))
        self.conn.commit()

    def stock_all(self, warehouse="") -> list[StockRow]:
        if warehouse:
            cur = self.conn.execute(
                "SELECT id, code, raw, name, cell, qty, nf_code, warehouse FROM stock "
                "WHERE warehouse=? ORDER BY code, cell, id", (warehouse,))
        else:
            cur = self.conn.execute(
                "SELECT id, code, raw, name, cell, qty, nf_code, warehouse FROM stock "
                "ORDER BY warehouse, code, cell, id")
        return [StockRow(*r) for r in cur.fetchall()]

    def stock_by_code(self, code) -> list[StockRow]:
        cur = self.conn.execute(
            "SELECT id, code, raw, name, cell, qty, nf_code, warehouse FROM stock "
            "WHERE code=? ORDER BY warehouse, id", (code,))
        return [StockRow(*r) for r in cur.fetchall()]

    def stock_by_base(self, base) -> list[StockRow]:
        """Все строки с данным «основанием» кода (без исполнения) — для подбора вариантов."""
        cur = self.conn.execute(
            "SELECT id, code, raw, name, cell, qty, nf_code, warehouse FROM stock "
            "WHERE code=? OR code LIKE ? ORDER BY warehouse, code, id",
            (base, base + "-%"))
        return [StockRow(*r) for r in cur.fetchall()]

    def stock_count(self, warehouse="") -> int:
        if warehouse:
            return self.conn.execute(
                "SELECT COUNT(*) FROM stock WHERE warehouse=?", (warehouse,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0]

    def stock_cells(self, warehouse="") -> list[str]:
        if warehouse:
            cur = self.conn.execute(
                "SELECT DISTINCT cell FROM stock WHERE warehouse=? AND cell IS NOT NULL AND cell!='' "
                "ORDER BY cell", (warehouse,))
        else:
            cur = self.conn.execute(
                "SELECT DISTINCT cell FROM stock WHERE cell IS NOT NULL AND cell!='' ORDER BY cell")
        return [r[0] for r in cur.fetchall()]

    def stock_search(self, q, warehouse="") -> list[StockRow]:
        like = f"%{q}%"
        if warehouse:
            cur = self.conn.execute(
                "SELECT id, code, raw, name, cell, qty, nf_code, warehouse FROM stock "
                "WHERE warehouse=? AND (code LIKE ? OR name LIKE ? OR raw LIKE ? OR cell LIKE ? OR nf_code LIKE ?) "
                "ORDER BY code, cell, id",
                (warehouse, like, like, like, like, like))
        else:
            cur = self.conn.execute(
                "SELECT id, code, raw, name, cell, qty, nf_code, warehouse FROM stock "
                "WHERE code LIKE ? OR name LIKE ? OR raw LIKE ? OR cell LIKE ? OR nf_code LIKE ? "
                "ORDER BY warehouse, code, cell, id",
                (like, like, like, like, like))
        return [StockRow(*r) for r in cur.fetchall()]

    def stock_update(self, row_id, code, raw, name, cell, qty, nf_code, warehouse):
        self.conn.execute(
            "UPDATE stock SET code=?, raw=?, name=?, cell=?, qty=?, nf_code=?, warehouse=? WHERE id=?",
            (code, raw, name, cell, qty, nf_code, warehouse, row_id))
        self.conn.commit()

    def stock_delete(self, row_id):
        self.conn.execute("DELETE FROM stock WHERE id=?", (row_id,))
        self.conn.commit()

    # ==================================================================
    #  Перемещения
    # ==================================================================
    def movement_create(self, warehouse, items: list[MovementItem]) -> int:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = self.conn.execute(
            "INSERT INTO movements(created_at, warehouse, status) VALUES (?,?,?)",
            (created_at, warehouse, DRAFT))
        mid = cur.lastrowid
        for it in items:
            self.conn.execute(
                "INSERT INTO movement_items(movement_id, row_num, nf_code, raw, code, name, cell, qty) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (mid, it.row_num, it.nf_code, it.raw, it.code, it.name, it.cell, it.qty))
        self.conn.commit()
        return mid

    def movement_get(self, movement_id) -> Movement | None:
        r = self.conn.execute(
            "SELECT id, created_at, warehouse, status FROM movements WHERE id=?", (movement_id,)).fetchone()
        if not r:
            return None
        m = Movement(id=r[0], created_at=r[1], warehouse=r[2], status=r[3])
        cur = self.conn.execute(
            "SELECT id, row_num, nf_code, raw, code, name, cell, qty FROM movement_items "
            "WHERE movement_id=? ORDER BY row_num, id", (movement_id,))
        for it in cur.fetchall():
            m.items.append(MovementItem(id=it[0], row_num=it[1], nf_code=it[2], raw=it[3],
                                        code=it[4], name=it[5], cell=it[6], qty=it[7]))
        return m

    def movement_list(self, status="") -> list[Movement]:
        if status:
            cur = self.conn.execute(
                "SELECT id, created_at, warehouse, status FROM movements WHERE status=? ORDER BY id DESC",
                (status,))
        else:
            cur = self.conn.execute(
                "SELECT id, created_at, warehouse, status FROM movements ORDER BY id DESC")
        rows = [Movement(id=r[0], created_at=r[1], warehouse=r[2], status=r[3]) for r in cur.fetchall()]
        for m in rows:
            tot = self.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(qty),0) FROM movement_items WHERE movement_id=?",
                (m.id,)).fetchone()
            m._n = tot[0]
            m._sum = tot[1]
        return rows

    def movement_set_item(self, item_id, nf_code, raw, code, name, cell, qty):
        self.conn.execute(
            "UPDATE movement_items SET nf_code=?, raw=?, code=?, name=?, cell=?, qty=? WHERE id=?",
            (nf_code, raw, code, name, cell, qty, item_id))
        self.conn.commit()

    def movement_add_item(self, movement_id, item: MovementItem):
        self.conn.execute(
            "INSERT INTO movement_items(movement_id, row_num, nf_code, raw, code, name, cell, qty) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (movement_id, item.row_num, item.nf_code, item.raw, item.code, item.name, item.cell, item.qty))
        self.conn.commit()

    def movement_delete_item(self, item_id):
        self.conn.execute("DELETE FROM movement_items WHERE id=?", (item_id,))
        self.conn.commit()

    def movement_set_warehouse(self, movement_id, warehouse):
        self.conn.execute("UPDATE movements SET warehouse=? WHERE id=?", (warehouse, movement_id))
        self.conn.commit()

    def movement_post(self, movement_id) -> Movement | None:
        """Провести перемещение: приход на склад + статус posted."""
        m = self.movement_get(movement_id)
        if m is None or m.status == POSTED:
            return m
        for it in m.items:
            self.stock_upsert(it.code, it.raw, it.name, it.cell, it.qty, it.nf_code, m.warehouse)
        self.conn.execute("UPDATE movements SET status=? WHERE id=?", (POSTED, movement_id))
        self.conn.commit()
        return self.movement_get(movement_id)

    def movement_delete(self, movement_id):
        self.conn.execute("DELETE FROM movement_items WHERE movement_id=?", (movement_id,))
        self.conn.execute("DELETE FROM movements WHERE id=?", (movement_id,))
        self.conn.commit()

    def close(self):
        self.conn.close()
