# -*- coding: utf-8 -*-
"""Формирование и печать документа «Задание на сборку» (по складам)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from .db import BEGOULEVO, BLUE, WAREHOUSES
from .spec import SpecResult


def _fmt(q) -> str:
    if q is None:
        return "-"
    q = float(q)
    if q == int(q):
        return str(int(q))
    return f"{q:g}"


def build_assembly_docs(result: SpecResult) -> dict[str, str]:
    """Тексты задания на сборку — по одному на каждый склад (для печати 2 листов)."""
    docs: dict[str, str] = {}
    for wh in WAREHOUSES:
        rows = [l for l in result.lines if l.warehouse == wh]
        if not rows:
            continue
        lines = [f"ЗАДАНИЕ НА СБОРКУ  —  {wh}", "=" * 46, ""]
        for l in rows:
            nm = l.name or l.code
            variant = "" if l.exact else "   [вариант — другое исполнение]"
            lines.append(f"  {l.code}  {nm}")
            lines.append(f"     ячейка {l.cell or '-'}   —   взять {_fmt(l.spec_qty)}{variant}")
            lines.append("")
        docs[wh] = "\n".join(lines)
    return docs


def list_printers() -> list[str]:
    """Список имён установленных принтеров (включая сетевые)."""
    if sys.platform.startswith("win"):
        try:
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            return [p[2] for p in win32print.EnumPrinters(flags, None, 1)]
        except Exception:
            return []
    try:
        out = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=10)
        names = []
        for ln in out.stdout.splitlines():
            if ln.startswith("printer "):
                names.append(ln.split()[1])
        return names
    except Exception:
        return []


def _write_temp(text: str, filename: str) -> str:
    d = tempfile.gettempdir()
    path = os.path.join(d, filename)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)
    return path


def print_text(text: str, filename: str = "zadanie_na_sborku.txt",
               printer: str | None = None) -> tuple[bool, str]:
    """Отправить текст на принтер.

    printer=None -> принтер по умолчанию. Возвращает (успех, путь к файлу).
    """
    path = _write_temp(text, filename)

    if sys.platform.startswith("win"):
        if printer:
            try:
                import win32print
                prev = win32print.GetDefaultPrinter()
                win32print.SetDefaultPrinter(printer)
                try:
                    os.startfile(path, "print")
                    return True, path
                finally:
                    try:
                        win32print.SetDefaultPrinter(prev)
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            os.startfile(path, "print")
            return True, path
        except OSError:
            return False, path
    else:
        cmd = ["lpr"]
        if printer:
            cmd += ["-P", printer]
        cmd.append(path)
        try:
            subprocess.run(cmd, check=True, timeout=30, capture_output=True)
            return True, path
        except Exception:
            try:
                subprocess.run(["lpr", path], check=True, timeout=30, capture_output=True)
                return True, path
            except Exception:
                return False, path
