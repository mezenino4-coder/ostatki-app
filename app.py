# -*- coding: utf-8 -*-
"""Утилита остатков склада — GUI (Tkinter + CustomTkinter).

Склады:
  - Центральный склад (старая база) — загружается из файла, только уменьшается.
  - Центральный синий склад (новая база) — пополняется перемещениями.

Кнопки:
  1. Пополнение (синий склад) — скрин перемещения (1–2 шт, Print Screen).
  2. Проверка наличия деталей — спецификация текстом → 2 списка + печать.
  3. Проверка базы (сверка с 1С УНФ) — таблица 3 колонки → рассинхрон.
  4. База / корректировка — вкладки старая/новая, поиск, сортировка.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

import customtkinter as ctk
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import importer, ocr
from core.check import check_store
from core.compare_1c import compare_1c, parse_1c_table
from core.db import NEW_WAREHOUSE, OLD_WAREHOUSE, Store
from core.movement import (MoveItem, analyze_movement, apply_movement,
                           merge_items)
from core.printout import build_assembly_doc, list_printers, print_text
from core.spec import check_spec, parse_spec_text

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

UI_FONT = "Noto Sans"
MONO_FONT = "DejaVu Sans Mono"

# --- палитра (общая для всех окон) ---
INK = "#1f2937"      # заголовки / основной текст
MUTED = "#6b7280"    # подписи / второстепенное
CARD_BG = "#ffffff"  # фон карточек
BORDER = "#e5e7eb"   # рамка карточек
SOFT_BG = "#f3f4f6"  # hover / вторичные кнопки
DANGER = "#dc2626"   # удаление / ошибки
OK_GREEN = "#16a34a"
WARN_ORANGE = "#ea580c"
WARN_YELLOW = "#ca8a04"


def _ctkfont(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=UI_FONT, size=size, weight=weight)


def _monofont(size: int) -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FONT, size=size)


def _bind_edit_shortcuts(widget):
    """Ctrl+C/V/X/A для текстовых полей.

    Tk на Linux/X11 не вешает эти хоткеи по умолчанию (вставка там — средней
    кнопкой мыши / <Insert>). Дублируем стандартные бинды поверх
    CTkTextbox / CTkEntry, чтобы работала обычная вставка Ctrl+V.
    """
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
            if hasattr(w, "tag_add"):          # tk.Text
                w.tag_add("sel", "1.0", "end")
                w.mark_set("insert", "1.0")
                w.see("insert")
            else:                              # tk.Entry
                w.selection_range(0, "end")
                w.icursor("end")
        except tk.TclError:
            pass
        return "break"

    for seq, fn in (("<Control-v>", _paste), ("<Control-c>", _copy),
                    ("<Control-x>", _cut), ("<Control-a>", _select_all)):
        widget.bind(seq, fn)


def _setup_style(root: tk.Tk) -> None:
    """Современная плоская тема + читаемый шрифт (вместо дефолтного Tk)."""
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkTooltipFont", "TkFixedFont"):
        try:
            tkfont.nametofont(name).configure(family=UI_FONT, size=11)
        except tk.TclError:
            pass

    # плоская современная тема (ttkthemes), фолбэк на дефолт
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
        style.map("Treeview",
                  background=[("selected", "#2563eb")],
                  foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading",
                  background=[("active", "#e5e7eb")])
        style.configure("TNotebook.Tab", font=(UI_FONT, 10), padding=(14, 6))
        style.configure("TCombobox", font=(UI_FONT, 11))
    except tk.TclError:
        pass


def _clipboard_image_to_file() -> str | None:
    """Прочитать изображение из буфера обмена и сохранить во временный файл.

    Возвращает путь к файлу или None, если в буфере нет картинки.
    Windows/macOS — через PIL ImageGrab; Linux — xclip (X11) / wl-paste (Wayland).
    """
    # Windows/macOS: PIL ImageGrab
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

    # Linux: xclip (X11) и wl-paste (Wayland)
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


class MovePasteDialog(ctk.CTkToplevel):
    """Окно добавления скриншотов перемещения: вставка из буфера (Ctrl+V) или файл."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Пополнение — скрин перемещения")
        self.geometry("680x440")
        self.paths: list[str] = []
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text="Скрин перемещения",
                     font=_ctkfont(16, "bold"), text_color=INK).pack(pady=(22, 2))
        ctk.CTkLabel(self, text="Нажмите Ctrl+V — вставится картинка из буфера обмена.\nИли выберите файл. Можно добавить несколько (например, 2 страницы).",
                     font=_ctkfont(12), text_color=MUTED, justify="center").pack(pady=(0, 12))

        self.preview_lbl = ctk.CTkLabel(self, text="Превью появится здесь",
                                        width=520, height=190, fg_color=SOFT_BG,
                                        corner_radius=10, text_color=MUTED,
                                        font=_ctkfont(12))
        self.preview_lbl.pack(padx=24, pady=(0, 10))

        self.count_lbl = ctk.CTkLabel(self, text="Скриншотов: 0",
                                      font=_ctkfont(12, "bold"), text_color=INK)
        self.count_lbl.pack(pady=(0, 12))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=24, pady=(0, 20))
        _secondary_button(btns, "Отмена", self._cancel, width=100).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Распознать", command=self._ok, width=130).pack(side="right", padx=4)
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


class App:
    def __init__(self, root):
        self.root = root
        root.title("Остатки склада")
        root.geometry("840x600")

        self.store = Store()

        # ---------- шапка ----------
        header = ctk.CTkFrame(root, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(22, 4))
        ctk.CTkLabel(header, text="Остатки склада",
                     font=ctk.CTkFont(family="Noto Sans", size=22, weight="bold"),
                     text_color=INK).pack()

        # ---------- карточки 2x2 ----------
        grid = ctk.CTkFrame(root, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=24, pady=14)
        grid.columnconfigure((0, 1), weight=1, uniform="card")
        grid.rowconfigure((0, 1), weight=1, uniform="card")

        icons = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
        cards = [
            ("Пополнение", "Синий склад", "popolnenie.png", self.load_move),
            ("Проверка наличия", "Деталей", "proverka.png", self.load_spec),
            ("Проверка базы", "Сверка с 1С", "baza.png", self.load_compare),
            ("База", "Корректировка", "korrekt.png", self.open_db_window),
        ]
        for i, (title, sub, icon, cmd) in enumerate(cards):
            card = self._make_card(grid, title, sub, os.path.join(icons, icon), cmd)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=8, pady=8)

        self._autoload_old()

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

    # ---------- сервис ----------
    def log(self, *args):
        """Лог в консоль (главное окно без журнала; результаты — в окнах)."""
        print(" ".join(str(a) for a in args))

    def _bg(self, fn):
        def run():
            try:
                fn()
            except Exception as e:
                self.root.after(0, messagebox.showerror, "Ошибка", str(e))
        threading.Thread(target=run, daemon=True).start()

    def _autoload_old(self):
        """Автозагрузка старой базы (Центральный склад) из файла рядом с программой."""
        if self.store.old_count() > 0:
            return
        base = (os.path.dirname(sys.executable)
                if getattr(sys, "frozen", False)
                else os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(base, "ostatki_po_skladu.tsv")
        if not os.path.exists(p):
            return
        try:
            n = importer.load_old_tsv(self.store, p)
            self.log(f"{OLD_WAREHOUSE} загружен автоматически: {n} позиций")
        except Exception as e:
            self.log(f"[!] Ошибка автозагрузки старой базы: {e}")

    # ---------- 1. старая база ----------
    def load_old(self):
        p = filedialog.askopenfilename(title=f"Файл остатков ({OLD_WAREHOUSE})",
                                       filetypes=[("TSV/CSV", "*.tsv *.csv"), ("Все", "*.*")])
        if not p:
            return

        def run():
            n = importer.load_old_tsv(self.store, p)
            self.root.after(0, lambda: self.log(f"{OLD_WAREHOUSE} загружен: {n} позиций"))
        self._bg(run)

    # ---------- 2. пополнение (перемещение) ----------
    def load_move(self):
        dlg = MovePasteDialog(self.root)
        self.root.wait_window(dlg)
        paths = dlg.paths
        if not paths:
            return

        # прогресс-окно (распознавание идёт синхронно, без фоновых потоков)
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
        self._prog_bar = ctk.CTkProgressBar(inner, mode="indeterminate",
                                            width=260, height=6)
        self._prog_bar.pack()
        self._prog_bar.start()
        prog.update_idletasks()
        prog.geometry(f"+{self.root.winfo_rootx() + 120}+{self.root.winfo_rooty() + 120}")
        self.root.update()

        try:
            lists = []
            source = ""
            source_name = ""
            for p in paths:
                img = ocr.load_image(p)
                img2, _ = ocr.deskew(img)
                res = ocr.run_ocr(img2, models_dir=MODELS_DIR)
                lists.append([MoveItem(code=it.code, raw=it.raw, qty=it.qty,
                                       nf_code=it.nf_code, cell=it.cell)
                              for it in res.items])
                if not source and res.source:
                    source, source_name = res.source, res.source_name
            items = merge_items(lists)
        except Exception as e:
            prog.destroy()
            messagebox.showerror("Ошибка", str(e))
            return
        prog.destroy()

        # с центрального склада без распознанной ячейки — уточнить вручную
        if source == "central":
            no_cell = [it for it in items if not it.cell]
            if no_cell:
                cell = (simpledialog.askstring(
                    "Ячейка",
                    "У части позиций не распознана ячейка. Укажите её:",
                    parent=self.root) or "").strip()
                for it in no_cell:
                    it.cell = cell

        result = analyze_movement(self.store, items, source=source)

        # одинаковые детали (код+ячейка) на разных строках документа — дубли
        from collections import Counter
        code_counts = Counter(it.code for it in items)
        dups = {code: n for code, n in code_counts.items() if n > 1}
        unique_count = len(code_counts)

        self.log(f"Пополнение: {len(items)} строк, {unique_count} уникальных деталей"
                 + (f" ({len(dups)} дублей объединено)" if dups else "")
                 + f", отправитель {source_name or 'не определён'}")

        # позиции, уже лежащие в новой базе — показать адрес и НФ-код
        seen: dict[str, object] = {}
        for it in items:
            if it.code in seen:
                continue
            ex = self.store.new_get(it.code)
            if ex is not None:
                seen[it.code] = ex
        if seen:
            lines = []
            for code, ex in seen.items():
                lines.append(f"• {ex.raw or ex.name or code} — ячейка {ex.cell or '—'}, НФ-код {ex.nf_code or '—'}")
            messagebox.showinfo(
                "Уже на складе",
                f"{len(seen)} из {unique_count} позиций уже есть в {NEW_WAREHOUSE}.\n"
                f"Количество будет прибавлено в существующую ячейку:\n\n" + "\n".join(lines))

        if not result.ok:
            msg = ["Расхождения сверки ячейки Центрального склада:"]
            for c in result.cell_checks:
                if c.ok:
                    continue
                if c.missing_in_move:
                    msg.append(f"Ячейка {c.cell}: остались — " + ", ".join(c.missing_in_move))
                if c.extra_in_move:
                    msg.append(f"Ячейка {c.cell}: лишние — " + ", ".join(c.extra_in_move))
            if not messagebox.askyesno("Расхождение", "\n".join(msg) + "\n\nПровести всё равно (принять фактический состав)?"):
                return
        apply_movement(self.store, result, confirm_force=True)
        if dups:
            dup_lines = "\n".join(
                f"• {code} — {n} строки (суммировано)" for code, n in sorted(dups.items()))
            messagebox.showinfo(
                "Пополнение",
                f"Готово: {unique_count} позиций добавлено в {NEW_WAREHOUSE}.\n\n"
                f"В документе {len(items)} строк, из них {len(dups)} деталей "
                f"повторяются дважды и объединены в одну (количество суммировано):\n\n"
                f"{dup_lines}")
        else:
            messagebox.showinfo("Пополнение",
                                f"Готово: {unique_count} позиций добавлено в {NEW_WAREHOUSE}.")

    # ---------- 3. проверка наличия деталей (спецификация) ----------
    def load_spec(self):
        dlg = TextPasteDialog(self.root, "Спецификация",
                              "Вставьте список деталей (код + количество):")
        self.root.wait_window(dlg)
        if not dlg.text:
            return

        def run():
            spec = parse_spec_text(dlg.text)
            result = check_spec(self.store, spec)

            def report():
                self.log("=== Проверка наличия деталей ===")
                self.log(f"Позиций в спецификации: {len(spec)}")
                self.log(f"Найдено — {NEW_WAREHOUSE}: {len(result.new_hits)}, "
                         f"{OLD_WAREHOUSE}: {len(result.old_hits)}")
                self.log("Списание с новой базы выполнено автоматически (по необходимому количеству).")
                SpecResultWindow(self, result)
            self.root.after(0, report)
        self._bg(run)

    # ---------- 4. сверка с 1С ----------
    def load_compare(self):
        dlg = TextPasteDialog(self.root, "Выгрузка 1С УНФ",
                              "Вставьте таблицу (наименование | унф-код | ячейка):")
        self.root.wait_window(dlg)
        if not dlg.text:
            return

        def run():
            rows = parse_1c_table(dlg.text)
            result = compare_1c(self.store, rows)

            def report():
                self.log("=== Проверка базы (сверка с 1С УНФ) ===")
                self.log(f"В 1С: {len(rows)} | В базе: {self.store.new_count()} | Совпало: {result.matched}")
                self.log(f"Только в 1С: {len(result.only_in_1c)} | Только у нас: {len(result.only_in_db)} | "
                         f"Ячейка разная: {len(result.cell_mismatch)}")
                CompareWindow(self, result)
            self.root.after(0, report)
        self._bg(run)

    # ---------- ручная корректировка ----------
    def open_db_window(self):
        DBWindow(self)


# ======================================================================
#  Вспомогательные примитивы оформления окон
# ======================================================================

def _window_header(parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
    """Шапка окна: заголовок + необязательная подпись."""
    head = ctk.CTkFrame(parent, fg_color="transparent")
    head.pack(fill="x", padx=22, pady=(18, 6))
    ctk.CTkLabel(head, text=title, font=_ctkfont(20, "bold"),
                 text_color=INK, anchor="w").pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(head, text=subtitle, font=_ctkfont(12),
                     text_color=MUTED, anchor="w").pack(anchor="w", pady=(2, 0))
    return head


def _secondary_button(parent, text, command, **kw):
    """Второстепенная кнопка (прозрачная, с рамкой)."""
    opts = dict(fg_color="transparent", text_color=INK, border_width=1,
                border_color=BORDER, hover_color=SOFT_BG)
    opts.update(kw)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def _fmt_qty(q) -> str:
    """Количество — целое число (без «.0»)."""
    if q is None:
        return ""
    try:
        f = float(q)
    except (TypeError, ValueError):
        return str(q)
    return str(int(f)) if f == int(f) else f"{f:g}"


# ======================================================================
#  Диалоги и окна результатов
# ======================================================================

class TextPasteDialog(ctk.CTkToplevel):
    """Окно вставки многострочного текста."""

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


class SpecResultWindow(ctk.CTkToplevel):
    """Результат проверки наличия: документ + печать «Задание на сборку»."""

    def __init__(self, app: "App", result):
        super().__init__(app.root)
        self.app = app
        self.result = result
        self.title("Задание на сборку")
        self.geometry("840x600")

        self.doc = build_assembly_doc(result)

        _window_header(
            self, "Задание на сборку",
            f"{NEW_WAREHOUSE}: {len(result.new_hits)} поз. · "
            f"{OLD_WAREHOUSE}: {len(result.old_hits)} поз.")

        card = ctk.CTkFrame(self, corner_radius=12, fg_color=CARD_BG,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True, padx=22, pady=8)
        txt = ctk.CTkTextbox(card, wrap="none", font=_monofont(12),
                             fg_color="transparent")
        txt.insert("1.0", self.doc)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=10, pady=10)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=22, pady=(4, 18))
        ctk.CTkLabel(foot, text="Принтер:", font=_ctkfont(12),
                     text_color=MUTED).pack(side="left", padx=(0, 8))
        printers = list_printers()
        self.printer_var = tk.StringVar(value="(по умолчанию)")
        ctk.CTkOptionMenu(foot, values=["(по умолчанию)"] + printers,
                          variable=self.printer_var, width=230,
                          font=_ctkfont(12)).pack(side="left", padx=4)
        _secondary_button(foot, "Сохранить в файл", self.do_save).pack(side="right", padx=4)
        ctk.CTkButton(foot, text="Печать", command=self.do_print, width=110).pack(side="right", padx=4)

    def do_print(self):
        pr = self.printer_var.get().strip()
        printer = None if pr in ("", "(по умолчанию)") else pr
        ok, path = print_text(self.doc, printer=printer)
        if ok:
            self.app.log("[печать] Документ отправлен на печать.")
        else:
            messagebox.showinfo("Печать", f"Не удалось отправить на печать.\nФайл сохранён: {path}")

    def do_save(self):
        p = filedialog.asksaveasfilename(defaultextension=".txt",
                                         filetypes=[("Текст", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.doc)
            self.app.log(f"Сохранено: {p}")


def _build_compare_text(result) -> str:
    """Текст рассинхрона для окна сверки с 1С."""
    lines = []
    if not result.only_in_1c and not result.only_in_db and not result.cell_mismatch:
        return "[OK] Рассинхрона нет. Новая база полностью совпадает с 1С.\n"
    if result.only_in_1c:
        lines.append(f"Только в 1С (нет в нашей базе): {len(result.only_in_1c)}")
        for r in result.only_in_1c:
            lines.append(f"  {r.code} | {r.name} | ячейка {r.cell or '-'}")
        lines.append("")
    if result.only_in_db:
        lines.append(f"Только у нас (нет в 1С): {len(result.only_in_db)}")
        for r in result.only_in_db:
            lines.append(f"  {r.code} | {r.name or r.raw} | ячейка {r.cell or '-'} | остаток {_fmt_qty(r.qty)}")
        lines.append("")
    if result.cell_mismatch:
        lines.append(f"Ячейка отличается: {len(result.cell_mismatch)}")
        for r, d in result.cell_mismatch:
            lines.append(f"  {r.code} | 1С: {r.cell or '-'} | у нас: {d}")
    return "\n".join(lines)


class CompareWindow(ctk.CTkToplevel):
    """Результат сверки новой базы с 1С УНФ."""

    def __init__(self, app: "App", result):
        super().__init__(app.root)
        self.title("Сверка с 1С УНФ")
        self.geometry("860x620")

        _window_header(
            self, "Сверка с 1С УНФ",
            f"Совпало {result.matched} · только в 1С: {len(result.only_in_1c)} · "
            f"только у нас: {len(result.only_in_db)} · ячейка разная: {len(result.cell_mismatch)}")

        # сводные карточки
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.pack(fill="x", padx=22, pady=(6, 2))
        for i in range(4):
            stats.columnconfigure(i, weight=1, uniform="stat")
        cards = [
            ("Совпало", result.matched, OK_GREEN),
            ("Только в 1С", len(result.only_in_1c), DANGER),
            ("Только у нас", len(result.only_in_db), WARN_ORANGE),
            ("Ячейка разная", len(result.cell_mismatch), WARN_YELLOW),
        ]
        for i, (label, val, color) in enumerate(cards):
            c = ctk.CTkFrame(stats, corner_radius=12, fg_color=CARD_BG,
                             border_width=1, border_color=BORDER)
            c.grid(row=0, column=i, sticky="nsew", padx=5)
            ctk.CTkLabel(c, text=str(val), font=_ctkfont(26, "bold"),
                         text_color=color).pack(pady=(12, 0))
            ctk.CTkLabel(c, text=label, font=_ctkfont(12),
                         text_color=MUTED).pack(pady=(0, 12))

        # детали
        card = ctk.CTkFrame(self, corner_radius=12, fg_color=CARD_BG,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True, padx=22, pady=8)
        txt = ctk.CTkTextbox(card, wrap="none", font=_monofont(12),
                             fg_color="transparent")
        txt.insert("1.0", _build_compare_text(result))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=10, pady=10)

        _secondary_button(self, "Закрыть", self.destroy, width=110).pack(pady=(2, 16))


class DBWindow(ctk.CTkToplevel):
    """Окно просмотра и ручной корректировки базы (старая + новая)."""

    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.store = app.store
        self.title("База и ручная корректировка")
        self.geometry("1100x680")

        # шапка + счётчики
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(18, 8))
        ctk.CTkLabel(head, text="База и ручная корректировка",
                     font=_ctkfont(20, "bold"), text_color=INK).pack(side="left")
        self.count_lbl = ctk.CTkLabel(head, text="", font=_ctkfont(12), text_color=MUTED)
        self.count_lbl.pack(side="right")

        # строка поиска
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=22, pady=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        self.search_entry = ctk.CTkEntry(bar, textvariable=self.search_var, width=320,
                     placeholder_text="Поиск: код / наименование / ячейка / НФ…",
                     font=_ctkfont(12))
        self.search_entry.pack(side="left")
        _bind_edit_shortcuts(self.search_entry)
        _secondary_button(bar, "Обновить", self.refresh, width=100).pack(side="left", padx=8)
        _secondary_button(bar, "Проверить базу", self.check, width=130).pack(side="right")

        # кнопки действий + операции с ячейками
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=22, pady=(0, 8))
        ctk.CTkButton(actions, text="Добавить", command=self.add, width=110).pack(side="left", padx=(0, 6))
        _secondary_button(actions, "Изменить", self.edit, width=110).pack(side="left", padx=6)
        _secondary_button(actions, "Удалить", self.delete, width=110,
                          text_color=DANGER, border_color="#fca5a5",
                          hover_color="#fef2f2").pack(side="left", padx=6)

        ctk.CTkLabel(actions, text="Ячейки (синий склад):", font=_ctkfont(12),
                     text_color=MUTED).pack(side="right", padx=(8, 6))
        _secondary_button(actions, "Слияние ячеек", self.merge_cells, width=130).pack(side="right", padx=4)
        _secondary_button(actions, "Перенос ячейки", self.move_cell, width=130).pack(side="right", padx=4)

        # вкладки старая/новая
        self.tabview = ctk.CTkTabview(self, corner_radius=12)
        self.tabview.pack(fill="both", expand=True, padx=22, pady=(0, 16))
        tab_old = self.tabview.add(OLD_WAREHOUSE)
        tab_new = self.tabview.add(NEW_WAREHOUSE)
        self.tree_old = self._make_tree(tab_old, "old")
        self.tree_new = self._make_tree(tab_new, "new")

        self._sort = {"old": ("code", False), "new": ("code", False)}
        self.refresh()

    def _make_tree(self, parent, table: str) -> ttk.Treeview:
        frame = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=10,
                             border_width=1, border_color=BORDER)
        frame.pack(fill="both", expand=True, pady=6)
        cols = ("code", "name", "cell") if table == "old" else ("code", "name", "cell", "qty")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        head = {"code": "Код", "name": "Наименование", "cell": "Ячейка", "qty": "Кол-во"}
        if table == "old":
            widths = {"code": 150, "name": 720, "cell": 130}
        else:
            widths = {"code": 140, "name": 640, "cell": 130, "qty": 90}
        for c in cols:
            tree.heading(c, text=head[c],
                         command=lambda col=c, t=table, tr=tree: self._sort_by(t, tr, col))
            tree.column(c, width=widths[c], anchor="w" if c != "qty" else "e",
                        stretch=(c == "name"))
        # зебра для читаемости (в палитре приложения)
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

    def _sort_by(self, table: str, tree: ttk.Treeview, col: str):
        cur_col, cur_rev = self._sort[table]
        reverse = not cur_rev if cur_col == col else False
        self._sort[table] = (col, reverse)
        self._fill(tree, table, self.search_var.get().strip())

    def _fill(self, tree: ttk.Treeview, table: str, q: str):
        tree.delete(*tree.get_children())
        if table == "old":
            rows = self.store.old_search(q) if q else self.store.old_all()
            data = [(r.nf_code or r.code, r.raw, r.cell, r.code) for r in rows]
        else:
            rows = self.store.new_search(q) if q else self.store.new_all()
            data = [(r.nf_code or "", r.raw, r.cell, r.qty, r.code) for r in rows]

        col, reverse = self._sort[table]
        idx = {"code": 0, "name": 1, "cell": 2, "qty": 3}[col]
        try:
            data.sort(key=lambda r: (r[idx] is None, float(r[idx]) if idx == 3 else str(r[idx]).lower()),
                      reverse=reverse)
        except (ValueError, TypeError):
            data.sort(key=lambda r: str(r[idx]).lower(), reverse=reverse)

        for i, row in enumerate(data):
            values = list(row[:-1])
            if table == "new":
                values[3] = _fmt_qty(values[3])
            tree.insert("", "end", iid=row[-1], values=values,
                        tags=("even" if i % 2 == 0 else "odd",))

    def refresh(self):
        q = self.search_var.get().strip()
        self._fill(self.tree_old, "old", q)
        self._fill(self.tree_new, "new", q)
        self.count_lbl.configure(
            text=f"{OLD_WAREHOUSE}: {self.store.old_count()} · "
                 f"{NEW_WAREHOUSE}: {self.store.new_count()}")

    def _active(self):
        name = self.tabview.get()
        if name == OLD_WAREHOUSE:
            return self.tree_old, "old"
        return self.tree_new, "new"

    def _selected(self):
        tree, table = self._active()
        sel = tree.selection()
        if not sel:
            return tree, table, None
        iid = sel[0]
        return tree, table, (iid, tree.item(iid, "values"))

    def add(self):
        _, table, _ = self._selected()
        dlg = EditDialog(self, title="Добавить позицию", table=table)
        self.wait_window(dlg)
        if dlg.result:
            if dlg.result[0] == "old":
                _, nf, raw, cell = dlg.result
                code, name = importer.split_full_name(raw)
                if not code:
                    messagebox.showwarning("Ошибка", "Не удалось извлечь код из наименования.")
                    return
                self.store.old_upsert(code, raw, name, cell, nf)
            else:
                _, nf, raw, cell, qty = dlg.result
                code, name = importer.split_full_name(raw)
                if not code:
                    messagebox.showwarning("Ошибка", "Не удалось извлечь код из наименования.")
                    return
                self.store.new_upsert_full(code, raw, name, cell, qty, nf)
            self.refresh()

    def edit(self):
        _, table, sel = self._selected()
        if not sel:
            messagebox.showinfo("Изменить", "Выберите строку.")
            return
        iid, values = sel
        if table == "old":
            cur = dict(nf=values[0], raw=values[1], cell=values[2])
            dlg = EditDialog(self, title=f"Изменить {values[0] or iid}", table=table, initial=cur)
            self.wait_window(dlg)
            if dlg.result:
                _, nf, raw, cell = dlg.result
                code, name = importer.split_full_name(raw)
                if not code:
                    messagebox.showwarning("Ошибка", "Не удалось извлечь код из наименования.")
                    return
                self.store.old_delete(iid)
                self.store.old_upsert(code, raw, name, cell, nf)
        else:
            cur = dict(nf=values[0], raw=values[1], cell=values[2], qty=values[3])
            dlg = EditDialog(self, title=f"Изменить {iid}", table=table, initial=cur)
            self.wait_window(dlg)
            if dlg.result:
                _, nf, raw, cell, qty = dlg.result
                code, name = importer.split_full_name(raw)
                if not code:
                    messagebox.showwarning("Ошибка", "Не удалось извлечь код из наименования.")
                    return
                self.store.new_delete(iid)
                self.store.new_upsert_full(code, raw, name, cell, qty, nf)
        self.refresh()

    def delete(self):
        tree, table = self._active()
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Удалить", "Выберите строки.", parent=self)
            return
        n = len(sel)
        if not messagebox.askyesno("Удалить", f"Удалить {n} поз. из базы «{table}»?", parent=self):
            return
        for iid in sel:
            if table == "old":
                self.store.old_delete(iid)
            else:
                self.store.new_delete(iid)
        self.refresh()

    def move_cell(self):
        cells = self.store.new_cells()
        if not cells:
            messagebox.showinfo("Перенос ячейки", "В новой базе нет ячеек.")
            return
        dlg = CellDialog(self, title="Перенос ячейки (поддон целиком)",
                         cells=cells, mode="move")
        self.wait_window(dlg)
        if dlg.result:
            src, dst = dlg.result
            try:
                n = self.store.move_cell(src, dst)
                self.app.log(f"[перенос] ячейка {src} -> {dst}: {n} позиций")
                self.refresh()
            except ValueError as e:
                messagebox.showwarning("Перенос ячейки", str(e))

    def merge_cells(self):
        cells = self.store.new_cells()
        if len(cells) < 2:
            messagebox.showinfo("Слияние ячеек", "Нужно минимум 2 ячейки в новой базе.")
            return
        dlg = CellDialog(self, title="Слияние ячеек (2 в 1)",
                         cells=cells, mode="merge")
        self.wait_window(dlg)
        if dlg.result:
            a, b, dst = dlg.result
            n = self.store.merge_cells(a, b, dst)
            self.app.log(f"[слияние] {a} + {b} -> {dst}: {n} позиций")
            self.refresh()

    def check(self):
        issues = check_store(self.store)
        win = ctk.CTkToplevel(self)
        win.title("Проверка базы")
        win.geometry("780x460")
        win.transient(self)

        _window_header(win, "Проверка базы",
                       f"{OLD_WAREHOUSE}: {self.store.old_count()} поз. · "
                       f"{NEW_WAREHOUSE}: {self.store.new_count()} поз.")

        card = ctk.CTkFrame(win, corner_radius=12, fg_color=CARD_BG,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True, padx=22, pady=8)
        txt = ctk.CTkTextbox(card, wrap="none", font=_monofont(12),
                             fg_color="transparent")
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


class CellDialog(ctk.CTkToplevel):
    """Диалог переноса целой ячейки или слияния двух ячеек (новая база)."""

    def __init__(self, parent, title: str, cells: list[str], mode: str):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.transient(parent)
        self.grab_set()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=22)
        ctk.CTkLabel(body, text=title, font=_ctkfont(16, "bold"),
                     text_color=INK).grid(row=0, column=0, columnspan=2,
                                          sticky="w", pady=(0, 14))

        def roww(label, widget, idx):
            ctk.CTkLabel(body, text=label, font=_ctkfont(12),
                         text_color=MUTED).grid(row=idx, column=0, sticky="e",
                                                padx=(0, 10), pady=7)
            widget.grid(row=idx, column=1, sticky="we", pady=7)

        self.src_cb = ctk.CTkComboBox(body, values=cells, width=270, font=_ctkfont(12))
        self.dst_cb = ctk.CTkComboBox(body, values=cells, width=270, font=_ctkfont(12))
        self.dst2 = None
        if mode == "move":
            roww("Из ячейки:", self.src_cb, 1)
            roww("В ячейку:", self.dst_cb, 2)
        else:
            roww("Ячейка 1:", self.src_cb, 1)
            roww("Ячейка 2:", self.dst_cb, 2)
            self.dst2 = ctk.CTkComboBox(body, values=cells, width=270, font=_ctkfont(12))
            roww("Итоговая ячейка:", self.dst2, 3)

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.grid(row=10, column=0, columnspan=2, pady=(18, 0))
        _secondary_button(btns, "Отмена", self.destroy, width=110).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="OK", command=self._ok, width=110).pack(side="left", padx=6)

    def _ok(self):
        a = self.src_cb.get().strip()
        b = self.dst_cb.get().strip()
        if not a or not b:
            messagebox.showwarning("Ошибка", "Заполните все ячейки.")
            return
        if a == b:
            messagebox.showwarning("Ошибка", "Ячейки должны различаться.")
            return
        if self.dst2 is not None:
            dst = self.dst2.get().strip() or a
            self.result = (a, b, dst)
        else:
            self.result = (a, b)
        self.destroy()


class EditDialog(ctk.CTkToplevel):
    """Диалог добавления/изменения одной позиции.

    Поля: Код (НФ), Наименование (полное), Ячейка,
    [Количество — только новая база]. Код-деталь берётся из наименования.
    """

    def __init__(self, parent, title: str, table: str, initial: dict | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.table = table
        initial = initial or {}
        self.transient(parent)
        self.grab_set()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=22)
        ctk.CTkLabel(body, text=title, font=_ctkfont(16, "bold"),
                     text_color=INK).grid(row=0, column=0, columnspan=2,
                                          sticky="w", pady=(0, 14))

        self.var_nf = tk.StringVar(value=initial.get("nf", ""))
        self.var_raw = tk.StringVar(value=initial.get("raw", ""))
        self.var_cell = tk.StringVar(value=initial.get("cell", ""))
        if table == "new":
            qty_init = initial.get("qty")
            self.var_qty = tk.StringVar(value=_fmt_qty(qty_init) if qty_init not in (None, "") else "")

        def row(label, var, idx):
            ctk.CTkLabel(body, text=label, font=_ctkfont(12),
                         text_color=MUTED).grid(row=idx, column=0, sticky="e",
                                                padx=(0, 10), pady=7)
            e = ctk.CTkEntry(body, textvariable=var, width=330,
                             font=_ctkfont(12))
            e.grid(row=idx, column=1, sticky="we", pady=7)
            _bind_edit_shortcuts(e)

        row("Код (НФ):", self.var_nf, 1)
        row("Наименование:", self.var_raw, 2)
        row("Ячейка:", self.var_cell, 3)
        if table == "new":
            row("Количество:", self.var_qty, 4)

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.grid(row=10, column=0, columnspan=2, pady=(18, 0))
        _secondary_button(btns, "Отмена", self.destroy, width=110).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="OK", command=self._ok, width=110).pack(side="left", padx=6)

    def _ok(self):
        nf = self.var_nf.get().strip()
        raw = self.var_raw.get().strip()
        cell = self.var_cell.get().strip()
        if not raw:
            messagebox.showwarning("Ошибка", "Наименование не может быть пустым.")
            return
        if self.table == "new":
            try:
                qty = float(self.var_qty.get().strip().replace(",", "."))
            except ValueError:
                messagebox.showwarning("Ошибка", "Количество должно быть числом.")
                return
            self.result = ("new", nf, raw, cell, qty)
        else:
            self.result = ("old", nf, raw, cell)
        self.destroy()


def main():
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    _setup_style(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
