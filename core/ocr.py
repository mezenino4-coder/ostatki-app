# -*- coding: utf-8 -*-
"""OCR скрина документа «Перемещение» + парсер строк таблицы.

Скрин — фото монитора, возможно под углом. Порядок:
  1. предобработка (deskew через HoughLines, как в plan_app);
  2. RapidOCR -> текст + bounding boxes + уверенность;
  3. группировка боксов по строкам (по y) и колонкам (по x);
  4. извлечение: код детали (номенклатура до «Штук»/ед.) и количество.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from .normalize import normalize_code, parse_qty


def load_image(path: str) -> np.ndarray:
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"не удалось прочитать изображение: {path}")
    return img


def deskew(img: np.ndarray, max_angle: float = 10.0) -> tuple[np.ndarray, float]:
    """Выровнять наклон (если есть). Возвращает (img, angle)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 100:
        return img, 0.0
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) > max_angle:
        return img, 0.0
    (h, w) = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE), angle


def _make_engine(models_dir: str | None = None):
    from rapidocr import RapidOCR
    params = {}
    if models_dir and __import__("os").path.isdir(models_dir):
        det = __import__("os").path.join(models_dir, "PP-OCRv6_det_small.onnx")
        cls = __import__("os").path.join(models_dir, "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
        rec = __import__("os").path.join(models_dir, "cyrillic_PP-OCRv5_rec_mobile.onnx")
        if __import__("os").path.exists(det):
            params["Det.model_path"] = det
        if __import__("os").path.exists(cls):
            params["Cls.model_path"] = cls
        if __import__("os").path.exists(rec):
            params["Rec.model_path"] = rec
    return RapidOCR(params=params) if params else RapidOCR()


@dataclass
class TextBox:
    text: str
    score: float
    cx: float
    cy: float
    x0: float
    x1: float
    y0: float
    y1: float


def _boxes_to_rows(boxes: list[TextBox], y_tol_ratio: float = 0.5) -> list[list[TextBox]]:
    """Сгруппировать боксы по строкам (по вертикальному центру)."""
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: b.cy)
    rows: list[list[TextBox]] = []
    cur = [boxes[0]]
    cur_cy = boxes[0].cy
    cur_h = max(boxes[0].y1 - boxes[0].y0, 1)
    for b in boxes[1:]:
        if abs(b.cy - cur_cy) <= y_tol_ratio * cur_h:
            cur.append(b)
        else:
            rows.append(sorted(cur, key=lambda x: x.cx))
            cur = [b]
            cur_cy = b.cy
            cur_h = max(b.y1 - b.y0, 1)
    rows.append(sorted(cur, key=lambda x: x.cx))
    return rows


@dataclass
class MoveItem:
    code: str      # нормализованный код детали (из «Наименования»)
    raw: str       # исходная строка наименования
    qty: float
    nf_code: str = ""   # НФ-код (отдельная колонка «Код»)
    cell: str = ""      # ячейка строки (построчно)


@dataclass
class OcrResult:
    items: list[MoveItem] = field(default_factory=list)
    cell: str = ""
    rows_text: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # полный текст строк (для отладки)
    source: str = ""        # склад-отправитель: "shelf" | "central" | "" (из шапки)
    source_name: str = ""   # имя склада-отправителя для отображения


def _strip_name_suffix(code: str) -> str:
    """Отрезать прилипшее название («Штук» -> ШТок/WroK/WTOK) от конца кода.

    Код в номенклатуре 1С оканчивается цифрой; название «Штук» OCR сливает
    с последним токеном кода. Если в конце стоит буквенный хвост — срезаем,
    но только если остаток всё ещё содержит цифру.
    """
    code = (code or "").strip()
    m = re.search(r"[А-Яа-яЁёA-Za-z]{2,}$", code)
    if m:
        rest = code[:m.start()].strip()
        if re.search(r"\d", rest):
            return rest
    return code


def _split_code_name(line: str) -> tuple[str, str]:
    """Из строки номенклатуры достать код и название.

    Номенклатура вида «Ш 0.32 160 00 00 03 Штук» -> код «Ш 0.32 160 00 00 03»,
    название «Штук». Название = последнее «слово» (или «Штук»/«шт»), код = всё до него.
    """
    line = (line or "").strip()
    if not line:
        return "", ""
    # отрезать суффикс-единицу/название: последний токен, если это буквы
    parts = line.split()
    if len(parts) >= 2 and re.fullmatch(r"[А-Яа-яЁёA-Za-z]{2,}", parts[-1]):
        name = parts[-1]
        code = " ".join(parts[:-1])
        return code, name
    # если название слиплось с кодом без пробела — вернуть всё как код
    return line, ""


def _find_qty_in_row(row: list[TextBox]) -> float | None:
    """Количество = число с запятой (45,000). Ищем бокс похожий на число."""
    for b in row:
        t = b.text.strip()
        if re.fullmatch(r"\d[\d\s\u00a0]*[,.]\d+", t):
            return parse_qty(t)
        if re.fullmatch(r"\d+", t):
            return parse_qty(t)
    return None


def _find_cell(lines: list[str]) -> str:
    """Поле «Ячейка» ищем в шапке/тексте (С-5-… или похожее)."""
    for ln in lines:
        m = re.search(r"[СC]-?\d+[-–—]\d+[-–—]?\d*", ln, re.IGNORECASE)
        if m:
            return m.group(0)
    return ""


def detect_source(lines: list[str]) -> tuple[str, str]:
    """Определить склад-отправитель по тексту скрина.

    Возвращает (source, имя склада).
      * "shelf"   — отправитель «Полка» (просто наполняем новую базу);
      * "central" — иначе Центральный склад (списание со старой + сверка).

    Ключевое слово — «полка»: оно появляется только когда отправитель — полка.
    Получатель всегда «Центральный синий склад», поэтому его не путаем.
    """
    text = " ".join(lines or []).lower()
    if "полка" in text:
        return "shelf", "Полка"
    return "central", "Центральный склад"


def _strip_row_number(code: str) -> str:
    """Отрезать прилипший номер строки N (цифры перед первой буквой).

    OCR сливает номер строки (1, 2, ... 10) с номенклатурой («1 ш.032...»).
    Отрезаем ведущее число, только если после него код начинается с БУКВЫ
    (в новой номенклатуре коды начинаются с буквы: Ш, АМ, К...).
    Коды старой базы, начинающиеся с цифры («09.051...»), не трогаем.
    """
    m = re.match(r"^(\d+)([А-Яа-яЁёA-Za-z])", code or "")
    if m:
        return code[m.end(1):]
    return code


# Границы колонок таблицы перемещения (доли ширины скрина 1920px).
# Реальная печатная форма 1С «Перемещение запасов»:
#   № (x~54-77) | Код=НФ (x~90-185) | Наименование (x~195-520) |
#   Кол-во (x~594-618) | Ед (x~615-641) | Ячейка (x~666-707) | Склад (x~746-874)
_COL_NUM = 0.045     # номер строки — левее
_COL_NF = 0.10       # НФ-код
_COL_NOM = 0.305     # наименование (деталь-код + название)
_COL_QTY = 0.345     # кол-во + ед.изм
_COL_CELL = 0.38     # ячейка (до x~730; «Склад» начинается с x~745)


def _column(x0: float, W: float) -> str:
    f = x0 / max(1.0, W)
    if f < _COL_NUM:
        return "num"
    if f < _COL_NF:
        return "nf"
    if f < _COL_NOM:
        return "nom"
    if f < _COL_QTY:
        return "qty"
    if f < _COL_CELL:
        return "cell"
    return "sklad"


def _detect_source_from_header(boxes: list[TextBox]) -> tuple[str, str]:
    """Склад-отправитель из шапки «Склад: значение».

    Находим метку «Склад:» слева (x<130) и читаем значение справа от неё
    на той же строке. «…Полка» -> shelf; «Центральный склад» -> central.
    Колонку «Склад» в таблице НЕ используем.
    """
    ordered = sorted(boxes, key=lambda b: (b.cy, b.cx))
    label = None
    for b in ordered:
        t = (b.text or "").lower()
        if b.x0 < 130 and ("скл" in t or "скп" in t):
            label = b
            break
    if label is None:
        return "", ""
    for b in ordered:
        if b.x0 >= label.x1 and abs(b.cy - label.cy) < 25:
            v = (b.text or "").lower()
            if "полка" in v:
                return "shelf", "Полка"
            if "центральн" in v:
                return "central", "Центральный склад"
            return "", (b.text or "").strip()
    return "", ""


def _ocr_qty_column(img: np.ndarray, engine) -> list[tuple[float, float]]:
    """Отдельный проход OCR по узкой колонке «Количество».

    В полном изображении одиночные цифры «1»/«2» сливаются с единицей («шт»)
    или не детектятся. Узкая полоса + апскейл 5x + бинаризация (OTSU) читают
    их стабильно. Возвращает список (y_center, qty) в координатах img.
    """
    h, w = img.shape[:2]
    x0, x1 = int(w * 0.302), int(w * 0.346)
    strip = img[:, x0:x1]
    big = cv2.resize(strip, None, fx=5.0, fy=5.0,
                     interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out = engine(bw)
    result: list[tuple[float, float]] = []
    if out is not None and out.txts:
        for box, text in zip(out.boxes, out.txts):
            pts = np.array(box, dtype=np.float32)
            ys = pts[:, 1] / 5.0
            t = (text or "").strip()
            m = re.match(r"^(\d{1,4})", t)
            if m:
                result.append((float(ys.mean()), parse_qty(m.group(1))))
    return result


def _qty_at_y(qty_col: list[tuple[float, float]], y: float, tol: float = 16.0):
    """Ближайшее количество из колонки по y (в пределах tol пикселей)."""
    best = None
    best_d = tol
    for qy, q in qty_col:
        d = abs(qy - y)
        if d < best_d:
            best_d = d
            best = q
    return best


def run_ocr(img: np.ndarray, models_dir: str | None = None) -> OcrResult:
    """Прогнать OCR по скрину и распарсить позиции перемещения.

    Строки определяются по колонке порядкового номера (1..N). Из каждой строки
    берём: НФ-код, деталь-код (первый токен «Наименования»), ячейку и количество.
    Количество читаем отдельным проходом по узкой колонке (надёжнее для цифр).
    """
    engine = _make_engine(models_dir)
    orig = img
    SCALE = 2.0
    img = cv2.resize(img, None, fx=SCALE, fy=SCALE,
                     interpolation=cv2.INTER_CUBIC)
    out = engine(img)
    W = float(img.shape[1]) / SCALE
    boxes: list[TextBox] = []
    if out is not None and out.txts:
        for box, text, score in zip(out.boxes, out.txts, out.scores):
            pts = np.array(box, dtype=np.float32) / SCALE
            xs = pts[:, 0]
            ys = pts[:, 1]
            boxes.append(TextBox(
                text=text, score=float(score),
                cx=float(xs.mean()), cy=float(ys.mean()),
                x0=float(xs.min()), x1=float(xs.max()),
                y0=float(ys.min()), y1=float(ys.max()),
            ))

    rows = _boxes_to_rows(boxes)
    qty_col = _ocr_qty_column(orig, engine)
    res = OcrResult()
    lines = [" ".join(b.text for b in r) for r in rows]
    res.lines = lines
    res.cell = _find_cell(lines)
    res.source, res.source_name = _detect_source_from_header(boxes)

    for r in rows:
        row_num = None
        row_num_y = None
        nf_code = ""
        nom_parts: list[str] = []
        qty = None
        cell = ""
        for b in r:
            t = (b.text or "").strip()
            if not t:
                continue
            col = _column(b.x0, W)
            if col == "num":
                if re.fullmatch(r"\d{1,3}", t):
                    row_num = t
                    row_num_y = b.cy
            elif col == "nf":
                if not nf_code and re.search(r"[ФΦ]?-?\d", t):
                    nf_code = t
            elif col == "nom":
                nom_parts.append(t)
            elif col == "qty":
                m = re.match(r"^(\d{1,4})", t)
                if m:
                    qty = parse_qty(m.group(1))
            elif col == "cell":
                cell = t
            # col == "sklad" — игнорируем (склад-отправитель берём из шапки)
        # только строки таблицы (есть порядковый номер)
        if row_num is None:
            continue
        nom = " ".join(nom_parts).strip()
        if not nom:
            continue
        # деталь-код = первый токен наименования
        code_raw = nom.split()[0]
        ncode = normalize_code(code_raw)
        ncode = _strip_row_number(ncode)
        # код детали всегда содержит цифру и точку; «∩1», «1:» — мусор
        if not re.search(r"\d", ncode) or "." not in ncode:
            continue
        # количество: надёжнее всего — по колонке (по y строки), fallback — полный OCR
        if qty_col and row_num_y is not None:
            q = _qty_at_y(qty_col, row_num_y)
            if q is not None:
                qty = q
        if qty is None:
            continue
        res.items.append(MoveItem(code=ncode, raw=nom, qty=qty,
                                  nf_code=nf_code, cell=cell))
        res.rows_text.append(nom)
    return res


def _fmt(q: float) -> str:
    return f"{q:g}"


def _strip_qty_tokens(line: str) -> str:
    """Убрать из строки токен-количество (число с запятой/точкой) и «шт». """
    parts = [p for p in line.split()
             if not re.fullmatch(r"\d[\d\s\u00a0]*[,.]\d+", p)
             and not re.fullmatch(r"\d+", p)
             and p.lower() not in ("шт", "штук")]
    return " ".join(parts)
