# -*- coding: utf-8 -*-
"""Утилита остатков склада — GUI (Tkinter + CustomTkinter).

Два склада, оба пополняются через «Перемещения»:
  - Центральный синий
  - Бегоулево

Пополнение: скрин -> черновик перемещения -> проверка/правка -> «Провести».
Проверка: спецификация -> задание на сборку (галочки) -> печать -> «Выдать».
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import ocr
from core.check import check_store
from core.db import (BEGOULEVO, BLUE, DRAFT, POSTED, WAREHOUSES, Movement,
                     MovementItem, Store)
from core.movement import merge_items
from core.normalize import split_full_name
from core.printout import build_assembly_docs, list_printers, print_text
from core.spec import check_spec, parse_spec_text

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

UI_FONT = "Noto Sans"
MONO_FONT = "DejaVu Sans Mono"

INK = "#1f2937"
MUTED = "#6b7280"
CARD_BG = "#ffffff"
BORDER = "#e5e7eb"
SOFT_BG = "#f3f4f6"
DANGER = "#dc2626"
OK_GREEN = "#16a34a"
WARN_ORANGE = "#ea580c"
WARN_YELLOW = "#ca8a04"


def _ctkfont(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=UI_FONT, size=size, weight=weight)


def _monofont(size: int) -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FONT, size=size)


def _bind_edit_shortcuts(widget):
    def _paste(e):
        e.widget.event_generate("<<Paste>>")
        return "break"

    def _copy(e):
        e.widget.event_generate("<<Copy>>")
        return "break"

    def _cut(e):
        e.widget.event_generate("<<Cut>>")
        return "break"

    def _select_all(e):
        w = e.widget
        try:
            if hasattr(w, "tag_add"):
                w.tag_add("sel", "1.0", "end")
                w.mark_set("insert", "1.0")
                w.see("insert")
            else:
                w.selection_range(0, "end")
                w.icursor("end")
        except tk.TclError:
            pass
        return "break"

    for seq, fn in (("<Control-v>", _paste), ("<Control-c>", _copy),
                    ("<Control-x>", _cut), ("<Control-a>", _select_all)):
        widget.bind(seq, fn)


def _setup_style(root: tk.Tk) -> None:
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkTooltipFont", "TkFixedFont"):
        try:
            tkfont.nametofont(name).configure(family=UI_FONT, size=11)
        except tk.TclError:
            pass
    try:
        from ttkthemes import ThemedStyle
        style = ThemedStyle(root)
        style.set_theme("arc")
    except Exception:
        style = ttk.Style(root)
    try:
        style.configure(".", font=(UI_FONT, 11))
        style.configure("TButton", font=(UI_FONT, 10), padding=(10, 5))
        style.configure("TLabel", font=(UI_FONT, 11))
        style.configure("TEntry", font=(UI_FONT, 11), padding=4)
        style.configure("Treeview", font=(UI_FONT, 10), rowheight=30,
                        background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=(UI_FONT, 10, "bold"),
                        background=SOFT_BG, foreground=INK, relief="flat")
        style.map("Treeview", background=[("selected", "#2563eb")],
                  foreground=[("selected", "#ffffff")])
        style.configure("TNotebook.Tab", font=(UI_FONT, 10), padding=(14, 6))
    except tk.TclError:
        pass


def _clipboard_image_to_file() -> str | None:
    try:
        from PIL import ImageGrab
        item = ImageGrab.grabclipboard()
        if isinstance(item, Image.Image):
            fd, path = tempfile.mkstemp(suffix=".png", prefix="ostatki_paste_")
            with os.fdopen(fd, "wb") as f:
                item.save(f, format="PNG")
            return path
        if isinstance(item, (list, tuple)):
            for fp in item:
                if isinstance(fp, str) and fp.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                    return fp
    except Exception:
        pass
    data = None
    ext = ".png"
    for cmd in (["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                ["xclip", "-selection", "clipboard", "-t", "image/jpeg", "-o"],
                ["wl-paste", "--type", "image/png"],
                ["wl-paste", "--type", "image/jpeg"]):
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if p.returncode == 0 and p.stdout:
            data = p.stdout
            if data.startswith(b"\xff\xd8\xff"):
                ext = ".jpg"
            break
    if not data or len(data) < 100:
        return None
    fd, path = tempfile.mkstemp(suffix=ext, prefix="ostatki_paste_")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def _window_header(parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
    head = ctk.CTkFrame(parent, fg_color="transparent")
    head.pack(fill="x", padx=22, pady=(18, 6))
    ctk.CTkLabel(head, text=title, font=_ctkfont(20, "bold"),
                 text_color=INK, anchor="w").pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(head, text=subtitle, font=_ctkfont(12),
                     text_color=MUTED, anchor="w").pack(anchor="w", pady=(2, 0))
    return head


def _secondary_button(parent, text, command, **kw):
    opts = dict(fg_color="transparent", text_color=INK, border_width=1,
                border_color=BORDER, hover_color=SOFT_BG)
    opts.update(kw)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def _fmt_qty(q) -> str:
    if q is None:
        return ""
    try:
        f = float(q)
    except (TypeError, ValueError):
        return str(q)
    return str(int(f)) if f == int(f) else f"{f:g}"


# ======================================================================
#  Диалоги
# ======================================================================

class MovePasteDialog(ctk.CTkToplevel):
    """Окно добавления скриншотов перемещения: вставка из буфера (Ctrl+V) или файл."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Новое перемещение — скрины")
        self.geometry("680x440")
        self.paths: list[str] = []
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text="Скрин перемещения", font=_ctkfont(16, "bold"),
                     text_color=INK).pack(pady=(22, 2))
        ctk.CTkLabel(self, text="Нажмите Ctrl+V — вставится картинка из буфера обмена.\nИли выберите файл. Можно несколько (например, 2 страницы).",
                     font=_ctkfont(12), text_color=MUTED, justify="center").pack(pady=(0, 12))

        self.preview_lbl = ctk.CTkLabel(self, text="Превью появится здесь",
                                        width=520, height=190, fg_color=SOFT_BG,
                                        corner_radius=10, text_color=MUTED, font=_ctkfont(12))
        self.preview_lbl.pack(padx=24, pady=(0, 10))

        self.count_lbl = ctk.CTkLabel(self, text="Скриншотов: 0",
                                      font=_ctkfont(12, "bold"), text_color=INK)
        self.count_lbl.pack(pady=(0, 12))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=24, pady=(0, 20))
        _secondary_button(btns, "Отмена", self._cancel, width=100).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Создать перемещение", command=self._ok, width=170).pack(side="right", padx=4)
        _secondary_button(btns, "Выбрать файл…", self._pick_file, width=140).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Вставить (Ctrl+V)", command=self._paste, width=160).pack(side="left", padx=4)

        self._preview_img = None
        self.bind("<Control-v>", self._on_paste_key)
        self.bind("<Control-V>", self._on_paste_key)
        self.after(80, self.focus_set)

    def _on_paste_key(self, e=None):
        self._paste()
        return "break"

    def _paste(self):
        p = _clipboard_image_to_file()
        if p is None:
            messagebox.showwarning("Буфер пуст",
                                   "В буфере обмена нет изображения.\nСделайте скриншот (Print Screen) и нажмите Ctrl+V.",
                                   parent=self)
            return
        self._add_path(p)

    def _pick_file(self):
        paths = filedialog.askopenfilenames(
            title="Скрин(ы) перемещения (Print Screen)",
            filetypes=[("Изображения", "*.jpg *.jpeg *.png *.bmp"), ("Все", "*.*")],
            parent=self)
        for p in paths:
            self._add_path(p)

    def _add_path(self, p):
        if p not in self.paths:
            self.paths.append(p)
        self.count_lbl.configure(text=f"Скриншотов: {len(self.paths)}")
        self._show_preview(p)

    def _show_preview(self, path):
        try:
            img = Image.open(path)
            img.thumbnail((520, 190))
            self._preview_img = ctk.CTkImage(img, size=(img.width, img.height))
            self.preview_lbl.configure(image=self._preview_img, text="")
        except Exception:
            self.preview_lbl.configure(text="(превью недоступно)", image=None)

    def _ok(self):
        self.destroy()

    def _cancel(self):
        self.paths = []
        self.destroy()


class TextPasteDialog(ctk.CTkToplevel):
    """Окно вставки многострочного текста (спецификация)."""

    def __init__(self, parent, title: str, prompt: str):
        super().__init__(parent)
        self.title(title)
        self.geometry("720x480")
        self.text = ""
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text=prompt, font=_ctkfont(13),
                     text_color=INK, anchor="w").pack(fill="x", padx=22, pady=(18, 8))

        self.box = ctk.CTkTextbox(self, wrap="none", font=_monofont(12),
                                  corner_radius=10, border_width=1,
                                  border_color=BORDER, fg_color=CARD_BG)
        self.box.pack(fill="both", expand=True, padx=22, pady=4)
        _bind_edit_shortcuts(self.box)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=22, pady=(4, 18))
        _secondary_button(btns, "Отмена", self.destroy, width=110).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="OK", command=self._ok, width=110).pack(side="right", padx=4)
        self.after(80, self.box.focus_set)

    def _ok(self):
        self.text = self.box.get("1.0", "end").strip()
        self.destroy()


class MovementItemDialog(ctk.CTkToplevel):
    """Добавление/изменение одной строки перемещения."""

    def __init__(self, parent, title: str, initial: dict | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        initial = initial or {}
        self.transient(parent)
        self.grab_set()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=22)
        ctk.CTkLabel(body, text=title, font=_ctkfont(16, "bold"),
                     text_color=INK).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

        self.var_nf = tk.StringVar(value=initial.get("nf", ""))
        self.var_raw = tk.StringVar(value=initial.get("raw", ""))
        self.var_cell = tk.StringVar(value=initial.get("cell", ""))
        self.var_qty = tk.StringVar(value=_fmt_qty(initial.get("qty")) if initial.get("qty") not in (None, "") else "")

        def row(label, var, idx):
            ctk.CTkLabel(body, text=label, font=_ctkfont(12),
                         text_color=MUTED).grid(row=idx, column=0, sticky="e", padx=(0, 10), pady=7)
            e = ctk.CTkEntry(body, textvariable=var, width=340, font=_ctkfont(12))
            e.grid(row=idx, column=1, sticky="we", pady=7)
            _bind_edit_shortcuts(e)

        row("Код (НФ):", self.var_nf, 1)
        row("Наименование:", self.var_raw, 2)
        row("Ячейка:", self.var_cell, 3)
        row("Количество:", self.var_qty, 4)

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.grid(row=10, column=0, columnspan=2, pady=(18, 0))
        _secondary_button(btns, "Отмена", self.destroy, width=110).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="OK", command=self._ok, width=110).pack(side="left", padx=6)

    def _ok(self):
        raw = self.var_raw.get().strip()
        if not raw:
            messagebox.showwarning("Ошибка", "Наименование не может быть пустым.")
            return
        try:
            qty = float(self.var_qty.get().strip().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Ошибка", "Количество должно быть числом.")
            return
        self.result = {
            "nf": self.var_nf.get().strip(),
            "raw": raw,
            "cell": self.var_cell.get().strip(),
            "qty": qty,
        }
        self.destroy()


# ======================================================================
#  Окно перемещения (черновик / проверка / провести)
# ======================================================================

class MovementWindow(ctk.CTkToplevel):
    """Просмотр и правка перемещения + «Провести»."""

    def __init__(self, app: "App", movement_id: int):
        super().__init__(app.root)
        self.app = app
        self.store = app.store
        self.movement_id = movement_id
        self.title(f"Перемещение №{movement_id}")
        self.geometry("1080x680")

        self._reload()
        self._build()
        self._fill()

    def _reload(self):
        self.m = self.store.movement_get(self.movement_id)

    def _build(self):
        # шапка
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(18, 6))
        ctk.CTkLabel(head, text=f"Перемещение №{self.m.id}",
                     font=_ctkfont(20, "bold"), text_color=INK).pack(side="left")
        status = "ПРОВЕДЕНО" if self.m.status == POSTED else "черновик"
        ctk.CTkLabel(head, text=f"({status} · {self.m.created_at})",
                     font=_ctkfont(12), text_color=MUTED).pack(side="left", padx=12)

        # склад + итого
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=22, pady=(0, 6))
        ctk.CTkLabel(bar, text="Склад (получатель):", font=_ctkfont(12),
                     text_color=MUTED).pack(side="left")
        self.wh_var = tk.StringVar(value=self.m.warehouse)
        self.wh_cb = ctk.CTkComboBox(bar, values=WAREHOUSES, variable=self.wh_var,
                                     width=220, font=_ctkfont(12),
                                     command=lambda _: self._save_warehouse())
        self.wh_cb.pack(side="left", padx=8)
        self.total_lbl = ctk.CTkLabel(bar, text="", font=_ctkfont(13, "bold"), text_color=INK)
        self.total_lbl.pack(side="right")

        # кнопки правки
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=22, pady=(0, 6))
        ctk.CTkButton(actions, text="Добавить", command=self.add_item, width=110).pack(side="left", padx=(0, 6))
        _secondary_button(actions, "Изменить", self.edit_item, width=110).pack(side="left", padx=6)
        _secondary_button(actions, "Удалить", self.del_item, width=110,
                          text_color=DANGER, border_color="#fca5a5",
                          hover_color="#fef2f2").pack(side="left", padx=6)

        # таблица
        card = ctk.CTkFrame(self, corner_radius=12, fg_color=CARD_BG,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True, padx=22, pady=6)
        cols = ("num", "nf", "name", "qty", "ed", "cell", "sklad")
        self.tree = ttk.Treeview(card, columns=cols, show="headings", selectmode="extended")
        heads = {"num": "№", "nf": "Код (НФ)", "name": "Наименование", "qty": "Кол-во",
                 "ed": "Ед", "cell": "Ячейка", "sklad": "Склад"}
        widths = {"num": 50, "nf": 150, "name": 480, "qty": 80, "ed": 50, "cell": 110, "sklad": 150}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="w" if c not in ("qty", "num") else "e",
                             stretch=(c == "name"))
        self.tree.tag_configure("odd", background="#f8fafc")
        self.tree.tag_configure("even", background="#ffffff")
        vsb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=(8, 0))
        card.rowconfigure(0, weight=1)
        card.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda e: self.edit_item())

        # низ
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=22, pady=(6, 18))
        if self.m.status == POSTED:
            _secondary_button(foot, "Закрыть", self.destroy, width=120).pack(side="right")
        else:
            _secondary_button(foot, "Закрыть (сохранить)", self.destroy, width=160).pack(side="right", padx=6)
            ctk.CTkButton(foot, text="Провести", command=self.do_post, width=140,
                          fg_color=OK_GREEN, hover_color="#15803d").pack(side="right", padx=6)

    def _save_warehouse(self):
        self.store.movement_set_warehouse(self.movement_id, self.wh_var.get().strip())
        self._reload()
        self._fill()

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, it in enumerate(self.m.items):
            self.tree.insert("", "end", iid=str(it.id),
                             values=(it.row_num, it.nf_code, it.raw, _fmt_qty(it.qty), "шт",
                                     it.cell, self.m.warehouse),
                             tags=("even" if i % 2 == 0 else "odd",))
        self.total_lbl.configure(text=f"Строк: {self.m.count} · ИТОГО: {_fmt_qty(self.m.total)}")

    def _selected_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add_item(self):
        dlg = MovementItemDialog(self, "Добавить строку")
        self.wait_window(dlg)
        if dlg.result:
            code, name = split_full_name(dlg.result["raw"])
            it = MovementItem(row_num=0, nf_code=dlg.result["nf"], raw=dlg.result["raw"],
                              code=code, name=name, cell=dlg.result["cell"], qty=dlg.result["qty"])
            self.store.movement_add_item(self.movement_id, it)
            self._reload()
            self._fill()

    def edit_item(self):
        iid = self._selected_id()
        if iid is None:
            return
        it = next((x for x in self.m.items if x.id == iid), None)
        if it is None:
            return
        dlg = MovementItemDialog(self, "Изменить строку",
                                 initial={"nf": it.nf_code, "raw": it.raw, "cell": it.cell, "qty": it.qty})
        self.wait_window(dlg)
        if dlg.result:
            code, name = split_full_name(dlg.result["raw"])
            self.store.movement_set_item(iid, dlg.result["nf"], dlg.result["raw"],
                                         code, name, dlg.result["cell"], dlg.result["qty"])
            self._reload()
            self._fill()

    def del_item(self):
        sel = self.tree.selection()
        if not sel:
            return
        if messagebox.askyesno("Удалить", f"Удалить {len(sel)} строк?"):
            for iid in sel:
                self.store.movement_delete_item(int(iid))
            self._reload()
            self._fill()

    def do_post(self):
        if not self.wh_var.get().strip():
            messagebox.showwarning("Провести", "Укажите склад.")
            return
        if not self.m.items:
            messagebox.showwarning("Провести", "В перемещении нет строк.")
            return
        self.store.movement_post(self.movement_id)
        messagebox.showinfo("Провести",
                            f"Перемещение №{self.movement_id} проведено.\n"
                            f"Приходовано {self.m.count} позиций на «{self.m.warehouse}», "
                            f"итого {_fmt_qty(self.m.total)} ед.")
        self.destroy()
        self.app.refresh_stock_counters()


# ======================================================================
#  Список перемещений
# ======================================================================

class MovementsWindow(ctk.CTkToplevel):
    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.store = app.store
        self.title("Перемещения")
        self.geometry("900x560")

        _window_header(self, "Перемещения", "Черновики и проведённые перемещения")

        card = ctk.CTkFrame(self, corner_radius=12, fg_color=CARD_BG,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True, padx=22, pady=8)
        cols = ("id", "date", "wh", "status", "count", "total")
        self.tree = ttk.Treeview(card, columns=cols, show="headings", selectmode="browse")
        heads = {"id": "№", "date": "Создано", "wh": "Склад", "status": "Статус",
                 "count": "Строк", "total": "Итого"}
        widths = {"id": 60, "date": 160, "wh": 200, "status": 120, "count": 70, "total": 80}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="w" if c not in ("id", "count", "total") else "e")
        self.tree.tag_configure("odd", background="#f8fafc")
        vsb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=(8, 0))
        card.rowconfigure(0, weight=1)
        card.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda e: self.open_movement())

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=22, pady=(6, 16))
        _secondary_button(foot, "Удалить", self.delete_movement, width=110,
                          text_color=DANGER, border_color="#fca5a5",
                          hover_color="#fef2f2").pack(side="right", padx=6)
        ctk.CTkButton(foot, text="Открыть", command=self.open_movement, width=120).pack(side="right", padx=6)
        _secondary_button(foot, "Обновить", self.refresh, width=110).pack(side="left")

        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.store.movement_list()):
            status = "проведено" if m.status == POSTED else "черновик"
            self.tree.insert("", "end", iid=str(m.id),
                             values=(m.id, m.created_at, m.warehouse or "-", status,
                                     getattr(m, "_n", 0), _fmt_qty(getattr(m, "_sum", 0))),
                             tags=("even" if i % 2 == 0 else "odd",))

    def _selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def open_movement(self):
        mid = self._selected()
        if mid is None:
            return
        w = MovementWindow(self.app, mid)
        self.wait_window(w)
        self.refresh()

    def delete_movement(self):
        mid = self._selected()
        if mid is None:
            return
        m = self.store.movement_get(mid)
        if m and m.status == POSTED:
            messagebox.showwarning("Удалить", "Проведённое перемещение удалить нельзя.")
            return
        if messagebox.askyesno("Удалить", f"Удалить перемещение №{mid}?"):
            self.store.movement_delete(mid)
            self.refresh()


# ======================================================================
#  Задание на сборку (проверка наличия + выдача)
# ======================================================================

class AssemblyTaskWindow(ctk.CTkToplevel):
    def __init__(self, app: "App", result):
        super().__init__(app.root)
        self.app = app
        self.store = app.store
        self.result = result
        self.title("Задание на сборку")
        self.geometry("920x640")

        _window_header(self, "Задание на сборку",
                       f"Позиций: {len(result.lines)} · не найдено: {len(result.not_found)}")

        # чекбоксы по строкам (в скролле)
        scroll = ctk.CTkScrollableFrame(self, width=880, height=430)
        scroll.pack(fill="both", expand=True, padx=22, pady=8)

        self.vars: dict[int, tk.BooleanVar] = {}
        current_wh = None
        for l in result.lines:
            if l.warehouse != current_wh:
                current_wh = l.warehouse
                ctk.CTkLabel(scroll, text=f"— {current_wh or 'без склада'} —",
                             font=_ctkfont(13, "bold"), text_color=INK,
                             anchor="w").pack(fill="x", pady=(10, 2))
            var = tk.BooleanVar(value=l.checked)
            self.vars[l.stock_id] = var
            variant = "" if l.exact else "  ⚠ вариант (другое исполнение)"
            txt = (f"{l.code}  {l.name or ''}   —  ячейка {l.cell or '-'}   "
                   f"—  нужно {_fmt_qty(l.spec_qty)}  (лежит {_fmt_qty(l.available)}){variant}")
            ctk.CTkCheckBox(scroll, text=txt, variable=var, font=_ctkfont(12),
                            text_color=INK).pack(anchor="w", pady=2)

        if result.not_found:
            ctk.CTkLabel(scroll, text="— не найдено ни точного, ни вариантов —",
                         font=_ctkfont(13, "bold"), text_color=DANGER,
                         anchor="w").pack(fill="x", pady=(14, 2))
            for s in result.not_found:
                ctk.CTkLabel(scroll, text=f"  {s.code}  {s.name}   (нужно {_fmt_qty(s.qty_needed)})",
                             font=_ctkfont(12), text_color=DANGER, anchor="w").pack(anchor="w", pady=1)

        # принтер + кнопки
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=22, pady=(6, 16))
        ctk.CTkLabel(foot, text="Принтер:", font=_ctkfont(12), text_color=MUTED).pack(side="left", padx=(0, 8))
        printers = list_printers()
        self.printer_var = tk.StringVar(value="(по умолчанию)")
        ctk.CTkOptionMenu(foot, values=["(по умолчанию)"] + printers,
                          variable=self.printer_var, width=230, font=_ctkfont(12)).pack(side="left", padx=4)

        _secondary_button(foot, "Закрыть", self.destroy, width=110).pack(side="right", padx=4)
        ctk.CTkButton(foot, text="Выдать", command=self.do_issue, width=110).pack(side="right", padx=4)
        ctk.CTkButton(foot, text="Печать", command=self.do_print, width=110).pack(side="right", padx=4)

    def _checked_lines(self):
        return [l for l in self.result.lines if self.vars.get(l.stock_id, tk.BooleanVar(value=l.checked)).get()]

    def do_print(self):
        docs = build_assembly_docs(self.result)
        if not docs:
            messagebox.showinfo("Печать", "Нет позиций для печати.")
            return
        pr = self.printer_var.get().strip()
        printer = None if pr in ("", "(по умолчанию)") else pr
        for wh, text in docs.items():
            ok, path = print_text(text, filename=f"zadanie_{wh}.txt".replace(" ", "_"), printer=printer)
            if not ok:
                messagebox.showinfo("Печать", f"Не удалось отправить на печать ({wh}).\nФайл: {path}")
                return
        messagebox.showinfo("Печать", f"Отправлено на печать: {len(docs)} лист(а).")

    def do_issue(self):
        lines = self._checked_lines()
        if not lines:
            messagebox.showinfo("Выдать", "Нет отмеченных позиций.")
            return
        written = 0
        for l in lines:
            take = min(l.spec_qty, l.available)
            if take > 0 and l.stock_id:
                self.store.stock_write_off_id(l.stock_id, take)
                written += take
        messagebox.showinfo("Выдать", f"Списано {_fmt_qty(written)} ед. по {len(lines)} позициям.")
        self.destroy()
        self.app.refresh_stock_counters()


# ======================================================================
#  База (остатки по складам)
# ======================================================================

class StockWindow(ctk.CTkToplevel):
    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.store = app.store
        self.title("База")
        self.geometry("1100x680")

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(18, 8))
        ctk.CTkLabel(head, text="База", font=_ctkfont(20, "bold"), text_color=INK).pack(side="left")
        self.count_lbl = ctk.CTkLabel(head, text="", font=_ctkfont(12), text_color=MUTED)
        self.count_lbl.pack(side="right")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=22, pady=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        self.search_entry = ctk.CTkEntry(bar, textvariable=self.search_var, width=320,
                     placeholder_text="Поиск: код / наименование / ячейка / НФ…", font=_ctkfont(12))
        self.search_entry.pack(side="left")
        _bind_edit_shortcuts(self.search_entry)
        _secondary_button(bar, "Обновить", self.refresh, width=100).pack(side="left", padx=8)
        _secondary_button(bar, "Проверить базу", self.check, width=130).pack(side="right")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=22, pady=(0, 8))
        ctk.CTkButton(actions, text="Добавить", command=self.add, width=110).pack(side="left", padx=(0, 6))
        _secondary_button(actions, "Изменить", self.edit, width=110).pack(side="left", padx=6)
        _secondary_button(actions, "Удалить", self.delete, width=110,
                          text_color=DANGER, border_color="#fca5a5",
                          hover_color="#fef2f2").pack(side="left", padx=6)

        self.tabview = ctk.CTkTabview(self, corner_radius=12)
        self.tabview.pack(fill="both", expand=True, padx=22, pady=(0, 16))
        self.tabs = {}
        for wh in WAREHOUSES:
            tab = self.tabview.add(wh)
            self.tabs[wh] = self._make_tree(tab)

        self.refresh()

    def _make_tree(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=10,
                             border_width=1, border_color=BORDER)
        frame.pack(fill="both", expand=True, pady=6)
        cols = ("code", "name", "cell", "qty", "nf")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        heads = {"code": "Код", "name": "Наименование", "cell": "Ячейка", "qty": "Кол-во", "nf": "НФ"}
        widths = {"code": 200, "name": 480, "cell": 110, "qty": 80, "nf": 150}
        for c in cols:
            tree.heading(c, text=heads[c])
            tree.column(c, width=widths[c], anchor="e" if c == "qty" else "w", stretch=(c == "name"))
        tree.tag_configure("odd", background="#f8fafc")
        tree.tag_configure("even", background="#ffffff")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=(8, 0))
        hsb.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(0, 8))
        return tree

    def _active(self):
        wh = self.tabview.get()
        return wh, self.tabs[wh]

    def _fill(self, wh, tree):
        tree.delete(*tree.get_children())
        q = self.search_var.get().strip()
        rows = self.store.stock_search(q, wh) if q else self.store.stock_all(wh)
        for i, r in enumerate(rows):
            tree.insert("", "end", iid=str(r.id),
                        values=(r.code, r.raw or r.name, r.cell, _fmt_qty(r.qty), r.nf_code),
                        tags=("even" if i % 2 == 0 else "odd",))

    def refresh(self):
        for wh, tree in self.tabs.items():
            self._fill(wh, tree)
        counts = " · ".join(f"{wh}: {self.store.stock_count(wh)} поз." for wh in WAREHOUSES)
        self.count_lbl.configure(text=counts)

    def _selected(self):
        wh, tree = self._active()
        sel = tree.selection()
        if not sel:
            return wh, None
        return wh, (int(sel[0]), tree.item(sel[0], "values"))

    def add(self):
        wh, _ = self._active()
        dlg = MovementItemDialog(self, title="Добавить позицию",
                                 initial={"nf": "", "raw": "", "cell": "", "qty": ""})
        self.wait_window(dlg)
        if dlg.result:
            code, name = split_full_name(dlg.result["raw"])
            if not code:
                messagebox.showwarning("Ошибка", "Не удалось извлечь код из наименования.")
                return
            self.store.stock_upsert(code, dlg.result["raw"], name, dlg.result["cell"],
                                    dlg.result["qty"], dlg.result["nf"], wh)
            self.refresh()

    def edit(self):
        wh, sel = self._selected()
        if not sel:
            messagebox.showinfo("Изменить", "Выберите строку.")
            return
        rid, values = sel
        rows = [r for r in self.store.stock_all(wh) if r.id == rid]
        if not rows:
            return
        r = rows[0]
        dlg = MovementItemDialog(self, title=f"Изменить {rid}",
                                 initial={"nf": r.nf_code, "raw": r.raw, "cell": r.cell, "qty": r.qty})
        self.wait_window(dlg)
        if dlg.result:
            code, name = split_full_name(dlg.result["raw"])
            if not code:
                messagebox.showwarning("Ошибка", "Не удалось извлечь код из наименования.")
                return
            self.store.stock_update(rid, code, dlg.result["raw"], name, dlg.result["cell"],
                                    dlg.result["qty"], dlg.result["nf"], wh)
            self.refresh()

    def delete(self):
        wh, _ = self._active()
        tree = self.tabs[wh]
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Удалить", "Выберите строки.", parent=self)
            return
        if messagebox.askyesno("Удалить", f"Удалить {len(sel)} поз. из «{wh}»?", parent=self):
            for iid in sel:
                self.store.stock_delete(int(iid))
            self.refresh()

    def check(self):
        issues = check_store(self.store)
        win = ctk.CTkToplevel(self)
        win.title("Проверка базы")
        win.geometry("780x460")
        win.transient(self)
        _window_header(win, "Проверка базы")
        card = ctk.CTkFrame(win, corner_radius=12, fg_color=CARD_BG,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True, padx=22, pady=8)
        txt = ctk.CTkTextbox(card, wrap="none", font=_monofont(12), fg_color="transparent")
        if not issues:
            txt.insert("1.0", "[OK] Проблем не найдено.\n")
        else:
            txt.insert("1.0", f"Найдено проблем: {len(issues)}\n\n")
            icon = {"error": "[x]", "warn": "[!]", "info": "[i]"}
            for i in issues:
                txt.insert("end", f"{icon.get(i.level, '•')} [{i.table}] {i.code or '-'}: {i.message}\n")
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        _secondary_button(win, "Закрыть", win.destroy, width=110).pack(pady=(2, 16))


# ======================================================================
#  Главное окно
# ======================================================================

class App:
    def __init__(self, root):
        self.root = root
        root.title("Остатки склада")
        root.geometry("840x600")

        self.store = Store()

        header = ctk.CTkFrame(root, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(22, 4))
        ctk.CTkLabel(header, text="Остатки склада",
                     font=ctk.CTkFont(family="Noto Sans", size=22, weight="bold"),
                     text_color=INK).pack()
        self.counters_lbl = ctk.CTkLabel(header, text="", font=_ctkfont(12), text_color=MUTED)
        self.counters_lbl.pack()

        grid = ctk.CTkFrame(root, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=24, pady=14)
        grid.columnconfigure((0, 1), weight=1, uniform="card")
        grid.rowconfigure((0, 1), weight=1, uniform="card")

        icons = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
        cards = [
            ("Пополнение", "новое перемещение из скрина", "popolnenie.png", self.load_move),
            ("Перемещения", "черновики и проведённые", "baza.png", self.load_movements),
            ("Проверка наличия", "задание на сборку", "proverka.png", self.load_spec),
            ("База", "остатки по складам", "korrekt.png", self.open_stock),
        ]
        for i, (title, sub, icon, cmd) in enumerate(cards):
            card = self._make_card(grid, title, sub, os.path.join(icons, icon), cmd)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=8, pady=8)

        self.refresh_stock_counters()

    def _make_card(self, parent, title, sub, icon_path, cmd):
        card = ctk.CTkFrame(parent, corner_radius=14, fg_color="#ffffff",
                            border_width=1, border_color="#e5e7eb")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(expand=True, fill="both", padx=16, pady=14)
        try:
            icon_img = ctk.CTkImage(Image.open(icon_path), size=(42, 42))
            ctk.CTkLabel(inner, image=icon_img, text="").pack(side="left", padx=(0, 14))
        except Exception:
            pass
        txt = ctk.CTkFrame(inner, fg_color="transparent")
        txt.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(txt, text=title,
                     font=ctk.CTkFont(family="Noto Sans", size=16, weight="bold"),
                     text_color="#1f2937", anchor="w").pack(anchor="w")
        if sub:
            ctk.CTkLabel(txt, text=sub,
                         font=ctk.CTkFont(family="Noto Sans", size=13),
                         text_color="#6b7280", anchor="w").pack(anchor="w")
        self._bind_click(card, cmd)
        return card

    def _bind_click(self, widget, cmd):
        widget.bind("<Button-1>", lambda e: cmd())
        for child in widget.winfo_children():
            self._bind_click(child, cmd)

    def log(self, *args):
        print(" ".join(str(a) for a in args))

    def _bg(self, fn):
        def run():
            try:
                fn()
            except Exception as e:
                self.root.after(0, messagebox.showerror, "Ошибка", str(e))
        threading.Thread(target=run, daemon=True).start()

    def refresh_stock_counters(self):
        self.counters_lbl.configure(
            text=" · ".join(f"{wh}: {self.store.stock_count(wh)}" for wh in WAREHOUSES))

    # ---------- 1. пополнение (новое перемещение) ----------
    def load_move(self):
        dlg = MovePasteDialog(self.root)
        self.root.wait_window(dlg)
        paths = dlg.paths
        if not paths:
            return

        prog = ctk.CTkToplevel(self.root)
        prog.title("Распознавание")
        prog.resizable(False, False)
        prog.transient(self.root)
        prog.attributes("-topmost", True)
        inner = ctk.CTkFrame(prog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=32, pady=(28, 22))
        ctk.CTkLabel(inner, text="Распознаю скрин(ы) перемещения…",
                     font=_ctkfont(14, "bold"), text_color=INK).pack()
        ctk.CTkLabel(inner, text="Это занимает около 10 секунд",
                     font=_ctkfont(12), text_color=MUTED).pack(pady=(4, 16))
        bar = ctk.CTkProgressBar(inner, mode="indeterminate", width=260, height=6)
        bar.pack()
        bar.start()
        prog.update_idletasks()
        prog.geometry(f"+{self.root.winfo_rootx() + 120}+{self.root.winfo_rooty() + 120}")
        self.root.update()

        try:
            lists = []
            warehouse = ""
            missing_qty = []
            for p in paths:
                img = ocr.load_image(p)
                img2, _ = ocr.deskew(img)
                res = ocr.run_ocr(img2, models_dir=MODELS_DIR)
                lists.append(res.items)
                if not warehouse and res.warehouse:
                    warehouse = res.warehouse
                missing_qty.extend(res.missing_qty)
            items = merge_items(lists)
        except Exception as e:
            prog.destroy()
            messagebox.showerror("Ошибка", str(e))
            return
        prog.destroy()

        if not items:
            messagebox.showwarning("Перемещение", "Не удалось распознать ни одной позиции.")
            return

        mitems = []
        for i, it in enumerate(items, 1):
            name = " ".join(it.raw.split()[1:]) if it.raw else ""
            mitems.append(MovementItem(row_num=i, nf_code=it.nf_code, raw=it.raw,
                                       code=it.code, name=name, cell=it.cell, qty=it.qty))
        mid = self.store.movement_create(warehouse, mitems)

        if missing_qty:
            mq_lines = "\n".join(f"• {m}" for m in missing_qty)
            messagebox.showwarning(
                "Внимание",
                f"Не распознано количество у {len(missing_qty)} позиций (поставлено 0):\n\n{mq_lines}\n\n"
                f"Поправьте их в окне перемещения.")

        MovementWindow(self, mid)


# ======================================================================

    # ---------- 2. список перемещений ----------
    def load_movements(self):
        MovementsWindow(self)

    # ---------- 3. проверка наличия (задание на сборку) ----------
    def load_spec(self):
        dlg = TextPasteDialog(self.root, "Спецификация",
                              "Вставьте список деталей (код + количество):")
        self.root.wait_window(dlg)
        if not dlg.text:
            return

        def run():
            spec = parse_spec_text(dlg.text)
            result = check_spec(self.store, spec)
            self.root.after(0, lambda: AssemblyTaskWindow(self, result))
        self._bg(run)

    # ---------- 4. база ----------
    def open_stock(self):
        StockWindow(self)


def main():
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    _setup_style(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
