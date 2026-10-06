# -*- coding: utf-8 -*-
"""Формирование и печать документа «Задание на сборку»."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from .db import OLD_WAREHOUSE, NEW_WAREHOUSE
from .spec import SpecResult


def _fmt(q) -> str:
    if q is None:
        return "-"
    q = float(q)
    if q == int(q):
        return str(int(q))
    return f"{q:g}"


def build_assembly_doc(result: SpecResult) -> str:
    """Текст документа «Задание на сборку»: заголовок + 2 списка."""
    lines = ["ЗАДАНИЕ НА СБОРКУ", "=" * 30, ""]

    both_empty = not result.new_hits and not result.old_hits
    if both_empty:
        lines.append("На Центральном складе ничего нет.")
        return "\n".join(lines)

    # --- новая база (синий склад) ---
    if result.new_hits:
        lines.append(f"{NEW_WAREHOUSE}:")
        for h in result.new_hits:
            nm = h.name or h.code
            lines.append(f"  {h.code}  {nm}  —  ячейка {h.cell or '-'}  —  нужно {_fmt(h.qty_needed)}")
    else:
        lines.append(f"На складе {NEW_WAREHOUSE} ничего нет.")
    lines.append("")

    # --- старая база (Центральный склад) ---
    if result.old_hits:
        lines.append(f"{OLD_WAREHOUSE}:")
        for h in result.old_hits:
            nm = h.name or h.code
            lines.append(f"  {h.code}  {nm}  —  ячейка {h.cell or '-'}  —  нужно {_fmt(h.qty_needed)}")
    else:
        lines.append(f"На {OLD_WAREHOUSE} ничего нет.")
    lines.append("")

    return "\n".join(lines)


def list_printers() -> list[str]:
    """Список имён установленных принтеров (включая сетевые)."""
    if sys.platform.startswith("win"):
        try:
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            return [p[2] for p in win32print.EnumPrinters(flags, None, 1)]
        except Exception:
            return []
    # Linux/macOS
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
                    # вернуть принтер по умолчанию (после постановки в очередь)
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
            # fallback без выбора принтера
            try:
                subprocess.run(["lpr", path], check=True, timeout=30, capture_output=True)
                return True, path
            except Exception:
                return False, path
