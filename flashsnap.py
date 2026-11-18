#!/usr/bin/env python3
"""
FlashSnap - flashcard al volo da qualsiasi testo selezionato, in qualsiasi app.

Installazione:   pip install pynput pyperclip
Avvio:           python flashsnap.py
"""
import json
import math
import queue
import random
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

try:
    import pyperclip
    from pynput import keyboard, mouse
except ImportError:
    print("Mancano delle librerie. Installa con:\n  pip install pynput pyperclip")
    sys.exit(1)

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# ───────────────────────── Tema Liquid Glass ─────────────────────────
BG = "#17191f"
PANEL = "#20232b"
PANEL2 = "#2a2e38"
PILL = "#343946"
HOVER = "#434959"
LINE = "#666e80"
EDGE = "#848d9e"
EDGE_ACTIVE = "#cdd7ec"
FG = "#f8fafc"
MUTED = "#b4bccb"
ACCENT = "#b7ccff"
GOOD = "#86efac"
BAD = "#fda4af"
FONT = "Segoe UI" if IS_WIN else ("Helvetica Neue" if IS_MAC else "DejaVu Sans")

DEFAULT_FOLDER = "Generale"
ALL_FOLDERS = "Tutte"
INTERVALS = [600, 86400, 3 * 86400, 7 * 86400, 16 * 86400, 35 * 86400]


# ───────────────────────── Archivio ─────────────────────────
class Store:
    def __init__(self):
        self.dir = Path.home() / ".flashsnap"
        self.dir.mkdir(exist_ok=True)
        self.path = self.dir / "cards.json"
        self.cards, self.meta = [], {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.cards = data.get("cards", [])
                self.meta = data.get("meta", {})
            except Exception:
                pass

        folders = list(self.meta.get("folders", []))
        if DEFAULT_FOLDER not in folders:
            folders.insert(0, DEFAULT_FOLDER)
        for c in self.cards:
            f = c.get("folder") or DEFAULT_FOLDER
            c["folder"] = f
            if f not in folders and f != ALL_FOLDERS:
                folders.append(f)
        self.meta["folders"] = folders

        act = self.meta.get("active_folder", ALL_FOLDERS)
        if act != ALL_FOLDERS and act not in folders:
            act = ALL_FOLDERS
        self.meta["active_folder"] = act

        tgt = self.meta.get("target_folder", DEFAULT_FOLDER)
        if tgt not in folders:
            tgt = DEFAULT_FOLDER
        self.meta["target_folder"] = tgt

    @property
    def folders(self):
        return self.meta["folders"]

    @property
    def active_folder(self):
        return self.meta["active_folder"]

    @property
    def target_folder(self):
        return self.meta["target_folder"]

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"cards": self.cards, "meta": self.meta},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def set_active_folder(self, name):
        if name == ALL_FOLDERS or name in self.folders:
            self.meta["active_folder"] = name
            if name != ALL_FOLDERS:
                self.meta["target_folder"] = name
            self.save()

    def set_target_folder(self, name):
        if name in self.folders:
            self.meta["target_folder"] = name
            self.save()

    def add_folder(self, name):
        name = " ".join(name.strip().split())[:28]
        if not name or name == ALL_FOLDERS:
            return None
        if name not in self.folders:
            self.folders.append(name)
            self.save()
        return name

    def delete_folder(self, name):
        if name == DEFAULT_FOLDER or name not in self.folders:
            return
        for c in self.cards:
            if c.get("folder") == name:
                c["folder"] = DEFAULT_FOLDER
        self.folders.remove(name)
        if self.meta.get("active_folder") == name:
            self.meta["active_folder"] = ALL_FOLDERS
        if self.meta.get("target_folder") == name:
            self.meta["target_folder"] = DEFAULT_FOLDER
        self.save()

    def cards_in(self, folder=None):
        f = self.active_folder if folder is None else folder
        if not f or f == ALL_FOLDERS:
            return self.cards
        return [c for c in self.cards if c.get("folder", DEFAULT_FOLDER) == f]

    def due(self, folder=None):
        now = time.time()
        return [c for c in self.cards_in(folder) if c["due"] <= now]

    def add(self, front, back, folder=None):
        f = folder or self.target_folder
        if f == ALL_FOLDERS or not f:
            f = DEFAULT_FOLDER
        if f not in self.folders:
            self.folders.append(f)
        self.cards.append({
            "id": time.time_ns(),
            "front": front,
            "back": back,
            "folder": f,
            "box": 0,
            "due": time.time(),
        })
        self.save()

    def update(self, card, front, back, folder=None):
        card["front"] = front
        card["back"] = back
        if folder and folder != ALL_FOLDERS:
            if folder not in self.folders:
                self.folders.append(folder)
            card["folder"] = folder
        self.save()

    def move_card(self, card, folder):
        if not folder or folder == ALL_FOLDERS:
            return
        if folder not in self.folders:
            self.folders.append(folder)
        card["folder"] = folder
        self.save()

    def delete(self, card):
        self.cards = [c for c in self.cards if c["id"] != card["id"]]
        self.save()

    def grade(self, card, level):
        now = time.time()
        if level == 0:
            card["box"] = 0
            card["due"] = now + INTERVALS[0]
        elif level == 1:
            card["due"] = now + INTERVALS[max(card["box"], 1)]
        else:
            card["box"] = min(card["box"] + 1, 5)
            card["due"] = now + INTERVALS[card["box"]]
        self.save()


# ───────────────────────── Widget di base ─────────────────────────
class Btn(tk.Label):
    def __init__(
        self,
        parent,
        text,
        cmd,
        fg=FG,
        bg=PILL,
        hover=HOVER,
        border=LINE,
        size=10,
        padx=14,
        pady=7,
        **kw,
    ):
        super().__init__(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            padx=padx,
            pady=pady,
            cursor="hand2",
            font=(FONT, size, "bold"),
            highlightthickness=1 if border else 0,
            highlightbackground=border if border else bg,
            **kw,
        )
        self._bg, self._hover = bg, hover
        self._border = border
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", lambda e: cmd())

    def _on_enter(self, _e):
        self.configure(bg=self._hover)
        if self._border:
            self.configure(highlightbackground=EDGE)

    def _on_leave(self, _e):
        self.configure(bg=self._bg)
        if self._border:
            self.configure(highlightbackground=self._border)


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return c.create_polygon(pts, smooth=True, **kw)


def clamp_to_screen(win, x, y, w, h, margin=8):
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    return max(margin, min(x, sw - w - margin)), max(margin, min(y, sh - h - margin))


# ───────────────────────── Finestrella trasparente (Pillola Vetro) ─────────────────────────
class Widget(tk.Toplevel):
    BASE_ALPHA = 0.62
    HOVER_ALPHA = 0.92

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self._alpha = None
        self._flashing = False
        self._leave_job = None
        self._last_text = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self._set_alpha(self.BASE_ALPHA, force=True)
        self.configure(bg=PANEL, highlightthickness=1, highlightbackground=EDGE)

        self.lbl = tk.Label(
            self,
            bg=PANEL,
            fg=FG,
            font=(FONT, 10, "bold"),
            padx=14,
            pady=8,
            cursor="hand2",
        )
        self.lbl.pack()
        self.refresh()
        self.update_idletasks()

        pos = app.store.meta.get("pos")
        if not pos:
            pos = [self.winfo_screenwidth() - 190, self.winfo_screenheight() - 140]
        x, y = clamp_to_screen(
            self, pos[0], pos[1], self.winfo_reqwidth(), self.winfo_reqheight()
        )
        self.geometry(f"+{x}+{y}")

        self._press = None
        self._moved = False
        for w in (self, self.lbl):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_motion)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<Button-3>", self._menu)
            w.bind("<Button-2>", self._menu)
            w.bind("<Control-Button-1>", self._menu)

    def _set_alpha(self, val, force=False):
        if force or self._alpha != val:
            self._alpha = val
            self.attributes("-alpha", val)

    def _on_enter(self, _e=None):
        if self._leave_job:
            self.after_cancel(self._leave_job)
            self._leave_job = None
        if not self._flashing:
            self._set_alpha(self.HOVER_ALPHA)

    def _on_leave(self, _e=None):
        if self._leave_job:
            self.after_cancel(self._leave_job)
        self._leave_job = self.after(60, self._check_leave)

    def _check_leave(self):
        self._leave_job = None
        if self._flashing:
            return
        try:
            px, py = self.winfo_pointerxy()
            wx, wy = self.winfo_rootx(), self.winfo_rooty()
            ww, wh = self.winfo_width(), self.winfo_height()
            inside = wx <= px <= wx + ww and wy <= py <= wy + wh
            self._set_alpha(self.HOVER_ALPHA if inside else self.BASE_ALPHA)
        except tk.TclError:
            pass

    def refresh(self):
        s = self.app.store
        cards = s.cards_in()
        text = f"▣ {len(cards)}"
        due = len(s.due())
        if due:
            text += f"  ↻{due}"
        if self.app.pending:
            text += f"  ● {'F' if self.app.pending[0] == 'front' else 'R'}"
        if self.app.paused:
            text += "  ⏸"
        if text != self._last_text:
            self._last_text = text
            self.lbl.configure(text=text)

    def flash(self, color=ACCENT):
        self._flashing = True
        self.lbl.configure(bg=color, fg=BG)
        self.configure(bg=color)
        self._set_alpha(1.0, force=True)

        def back():
            self._flashing = False
            self.lbl.configure(bg=PANEL, fg=FG)
            self.configure(bg=PANEL)
            self._check_leave()

        self.after(480, back)

    def _on_press(self, e):
        self._press = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())
        self._moved = False

    def _on_motion(self, e):
        if not self._press:
            return
        dx, dy = e.x_root - self._press[0], e.y_root - self._press[1]
        if abs(dx) + abs(dy) > 4:
            self._moved = True
        if self._moved:
            self.geometry(f"+{self._press[2] + dx}+{self._press[3] + dy}")

    def _on_release(self, e):
        if self._moved:
            self.app.store.meta["pos"] = [self.winfo_x(), self.winfo_y()]
            self.app.store.save()
        else:
            self.app.open_review()
        self._press = None

    def _menu(self, e):
        m = tk.Menu(
            self,
            tearoff=0,
            bg=PANEL2,
            fg=FG,
            activebackground=HOVER,
            activeforeground=FG,
            bd=0,
            font=(FONT, 10),
        )
        m.add_command(label="Ripassa", command=self.app.open_review)

        sub = tk.Menu(
            m,
            tearoff=0,
            bg=PANEL2,
            fg=FG,
            activebackground=HOVER,
            activeforeground=FG,
            bd=0,
            font=(FONT, 10),
        )
        act = self.app.store.active_folder
        sub.add_command(
            label=("✓ " if act == ALL_FOLDERS else "   ") + "Tutte le cartelle",
            command=lambda: self._pick_folder(ALL_FOLDERS),
        )
        sub.add_separator()
        for f in self.app.store.folders:
            sub.add_command(
                label=("✓ " if act == f else "   ") + f,
                command=lambda f=f: self._pick_folder(f),
            )
        m.add_cascade(label=f"Cartella ({act})", menu=sub)

        m.add_command(
            label="Riprendi cattura" if self.app.paused else "Pausa cattura",
            command=self.app.toggle_pause,
        )
        if self.app.pending:
            m.add_command(label="Annulla carta in sospeso", command=self.app.cancel_pending)
        m.add_separator()
        m.add_command(label="Esci", command=self.app.quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _pick_folder(self, name):
        self.app.store.set_active_folder(name)
        self.refresh()
        if self.app.review:
            self.app.review.refresh_folder_change()


# ───────────────────────── Pannello di scelta Fronte/Retro + Cartella ─────────────────────────
class Popup(tk.Toplevel):
    W = 356

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.text = ""
        self.visible = False
        self.writing = False
        self.menu_open = False
        self._job = None
        self._last_pick = 0.0
        self._chosen_side = None
        self._folder = app.store.target_folder

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.90)
        self.configure(bg=EDGE)

        self.box = tk.Frame(self, bg=PANEL)
        self.box.pack(padx=1, pady=1)

        # Barra superiore Popup: scelta cartella + chiudi
        top = tk.Frame(self.box, bg=PANEL)
        top.pack(fill="x", padx=12, pady=(10, 4))

        self.folder_btn = Btn(
            top,
            f"📁 {self._folder} ▾",
            self._open_folder_menu,
            fg=ACCENT,
            bg=PANEL2,
            border=LINE,
            size=9,
            padx=9,
            pady=3,
        )
        self.folder_btn.pack(side="left")

        close_lbl = tk.Label(
            top, text="✕", bg=PANEL, fg=MUTED, font=(FONT, 10, "bold"), cursor="hand2"
        )
        close_lbl.pack(side="right", padx=(6, 0))
        close_lbl.bind("<Button-1>", lambda e: self.hide())
        close_lbl.bind("<Enter>", lambda e: close_lbl.configure(fg=FG))
        close_lbl.bind("<Leave>", lambda e: close_lbl.configure(fg=MUTED))

        # Mini campo inline per creare una cartella al volo dal Popup
        self.new_folder_row = tk.Frame(self.box, bg=PANEL)
        nf_border = tk.Frame(
            self.new_folder_row, bg=PANEL2, highlightthickness=1, highlightbackground=EDGE
        )
        nf_border.pack(side="left", fill="x", expand=True)
        self.nf_entry = tk.Entry(
            nf_border,
            bg=PANEL2,
            fg=FG,
            insertbackground=ACCENT,
            font=(FONT, 9),
            bd=0,
            highlightthickness=0,
        )
        self.nf_entry.pack(fill="x", padx=8, pady=4)
        self.nf_entry.bind("<Return>", lambda e: self._confirm_new_folder())
        self.nf_entry.bind("<Escape>", lambda e: self._close_new_folder())
        Btn(
            self.new_folder_row,
            "Crea",
            self._confirm_new_folder,
            fg=GOOD,
            bg=PILL,
            size=9,
            padx=10,
            pady=3,
        ).pack(side="left", padx=(6, 0))

        self.preview = tk.Label(
            self.box,
            bg=PANEL,
            fg=FG,
            font=(FONT, 10),
            justify="left",
            anchor="w",
            wraplength=self.W - 28,
        )
        self.preview.pack(fill="x", padx=14, pady=(4, 4))

        self.status = tk.Label(
            self.box,
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            anchor="w",
            wraplength=self.W - 28,
        )
        self.status.pack(fill="x", padx=14)

        # Vista 1: scelta Fronte / Retro
        self.choice_frame = tk.Frame(self.box, bg=PANEL)
        self.b_front = Btn(self.choice_frame, "Fronte", lambda: self._pick("front"))
        self.b_back = Btn(self.choice_frame, "Retro", lambda: self._pick("back"))
        self.b_front.pack(side="left", expand=True, fill="x", padx=4)
        self.b_back.pack(side="left", expand=True, fill="x", padx=4)

        # Vista 2: scrittura parte mancante
        self.write_frame = tk.Frame(self.box, bg=PANEL)
        input_border = tk.Frame(
            self.write_frame, bg=PANEL2, highlightthickness=1, highlightbackground=EDGE
        )
        input_border.pack(fill="x", padx=14, pady=(8, 8))
        self.write_box = tk.Text(
            input_border,
            bg=PANEL2,
            fg=FG,
            insertbackground=ACCENT,
            font=(FONT, 10),
            height=4,
            width=36,
            wrap="word",
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=6,
        )
        self.write_box.pack(fill="both", expand=True)
        self.write_box.bind("<Return>", self._on_enter_key)
        self.write_box.bind("<Escape>", lambda e: self._cancel_write())

        w_btns = tk.Frame(self.write_frame, bg=PANEL)
        w_btns.pack(fill="x", padx=10, pady=(0, 10))
        Btn(w_btns, "Annulla", self._cancel_write, fg=MUTED, bg=PANEL2, size=9).pack(
            side="left", padx=4
        )
        Btn(w_btns, "Salva · Invio", self._save_written, fg=GOOD, bg=PILL, size=9).pack(
            side="right", expand=True, fill="x", padx=4
        )

        self.withdraw()

    def _sync_folder_btn(self):
        if self._folder not in self.app.store.folders:
            self._folder = self.app.store.target_folder
        self.folder_btn.configure(text=f"📁 {self._folder} ▾")

    def _open_folder_menu(self):
        self._last_pick = time.monotonic()
        self.menu_open = True
        if self._job:
            self.after_cancel(self._job)
            self._job = None

        m = tk.Menu(
            self,
            tearoff=0,
            bg=PANEL2,
            fg=FG,
            activebackground=HOVER,
            activeforeground=FG,
            bd=0,
            font=(FONT, 10),
        )
        for f in self.app.store.folders:
            m.add_command(
                label=("✓ " if f == self._folder else "   ") + f,
                command=lambda f=f: self._select_folder(f),
            )
        m.add_separator()
        m.add_command(label="+ Nuova cartella…", command=self._show_new_folder)

        bx = self.folder_btn.winfo_rootx()
        by = self.folder_btn.winfo_rooty() + self.folder_btn.winfo_height() + 2
        try:
            m.tk_popup(bx, by)
        finally:
            m.grab_release()
            self.after(120, lambda: setattr(self, "menu_open", False))

    def _select_folder(self, name):
        self._folder = name
        self.app.store.set_target_folder(name)
        self._sync_folder_btn()

    def _show_new_folder(self):
        self.new_folder_row.pack(fill="x", padx=12, pady=(2, 6), before=self.preview)
        self.nf_entry.delete(0, "end")
        self.update_idletasks()
        self.after(20, lambda: (self.focus_force(), self.nf_entry.focus_set()))

    def _close_new_folder(self):
        self.new_folder_row.pack_forget()
        self.update_idletasks()

    def _confirm_new_folder(self):
        name = self.app.store.add_folder(self.nf_entry.get())
        self._close_new_folder()
        if name:
            self._select_folder(name)
            if self.app.review:
                self.app.review.refresh_folder_change()
        if self.writing:
            self.after(20, self._focus_input)

    def show(self, text, x, y):
        self.text = text
        self.writing = False
        self.new_folder_row.pack_forget()
        self.write_frame.pack_forget()
        self.choice_frame.pack(fill="x", padx=10, pady=(8, 10))

        self._folder = self.app.store.target_folder
        self._sync_folder_btn()

        short = text if len(text) <= 130 else text[:127] + "…"
        self.preview.configure(text=f"“{short}”")

        p = self.app.pending
        self.b_front.configure(text="Fronte", fg=FG)
        self.b_back.configure(text="Retro", fg=FG)
        if p:
            other = "back" if p[0] == "front" else "front"
            ps = p[1] if len(p[1]) <= 40 else p[1][:37] + "…"
            self.status.configure(
                text=f"Passo 2/2 · {'Fronte' if p[0] == 'front' else 'Retro'} salvato: «{ps}»",
                fg=ACCENT,
            )
            if other == "back":
                self.b_back.configure(text="Retro · Crea ✓", fg=GOOD)
            else:
                self.b_front.configure(text="Fronte · Crea ✓", fg=GOOD)
        else:
            self.status.configure(
                text="Scegli lato (poi scrivi o seleziona l'altro)", fg=MUTED
            )

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        px, py = clamp_to_screen(self, x + 14, y + 18, w, h)
        self.geometry(f"+{px}+{py}")
        self.deiconify()
        self.lift()
        self.update_idletasks()
        self.visible = True

        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(7500, self.hide)

    def _enter_write_mode(self, side):
        self.writing = True
        self._chosen_side = side
        if self._job:
            self.after_cancel(self._job)
            self._job = None

        missing = "Retro" if side == "front" else "Fronte"
        chosen = "Fronte" if side == "front" else "Retro"
        short = self.text if len(self.text) <= 95 else self.text[:92] + "…"
        self.preview.configure(text=f"{chosen}: “{short}”")
        self.status.configure(
            text=f"Scrivi il {missing} (Invio salva · Shift+Invio a capo):",
            fg=ACCENT,
        )

        self.choice_frame.pack_forget()
        self.write_frame.pack(fill="x")
        self.write_box.delete("1.0", "end")

        self.update_idletasks()
        px, py = clamp_to_screen(
            self,
            self.winfo_x(),
            self.winfo_y(),
            self.winfo_reqwidth(),
            self.winfo_reqheight(),
        )
        self.geometry(f"+{px}+{py}")
        self.lift()
        self.after(20, self._focus_input)

    def _focus_input(self):
        if self.visible and self.writing:
            try:
                self.focus_force()
                self.write_box.focus_set()
            except tk.TclError:
                pass

    def _on_enter_key(self, e):
        if e.state & 0x1:
            return
        self._save_written()
        return "break"

    def _save_written(self):
        val = self.write_box.get("1.0", "end-1c").strip()
        if not val:
            missing = "Retro" if self._chosen_side == "front" else "Fronte"
            self.status.configure(text=f"⚠ Scrivi il {missing} prima di salvare!", fg=BAD)
            self.after(1500, self._restore_status_fg)
            return
        self._last_pick = time.monotonic()
        other = "back" if self._chosen_side == "front" else "front"
        self.app.choose(other, val, folder=self._folder, open_writer=False)

    def _restore_status_fg(self):
        if self.visible and self.writing:
            self.status.configure(fg=ACCENT)

    def _cancel_write(self):
        self.app.cancel_pending()
        self.hide()

    def has_unsaved_input(self):
        return self.writing and bool(self.write_box.get("1.0", "end-1c").strip())

    def _pick(self, side):
        self._last_pick = time.monotonic()
        self.app.choose(side, self.text, folder=self._folder, open_writer=True)

    def hide(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.writing = False
        self.visible = False
        self.withdraw()


# ───────────────────────── Finestra di ripasso (Liquid Glass + Cartelle) ─────────────────────────
class Review(tk.Toplevel):
    CW, CH = 560, 316

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.store = app.store
        self.queue, self.i, self.current = [], 0, None
        self.showing_back = False
        self.busy = False
        self.editing = False
        self.browsing_folders = False
        self._everything = False
        self._press = None
        self._refit_job = None
        self._edit_folder = DEFAULT_FOLDER

        self.title("FlashSnap - Ripasso")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.84)
        self.configure(bg=PANEL)

        w, h = 620, 560
        self.geometry(
            f"{w}x{h}+{(self.winfo_screenwidth() - w) // 2}+"
            f"{(self.winfo_screenheight() - h) // 2}"
        )
        self.protocol("WM_DELETE_WINDOW", self.close)

        main_frame = tk.Frame(
            self, bg=PANEL, highlightthickness=1, highlightbackground=EDGE
        )
        main_frame.pack(fill="both", expand=True)

        # Top bar stile pillola App Library
        top = tk.Frame(main_frame, bg=PANEL)
        top.pack(fill="x", padx=28, pady=(18, 10))

        self.folder_pill = Btn(
            top,
            f"📁 {self.store.active_folder} ▾",
            self.toggle_folders_view,
            fg=FG,
            bg=PILL,
            hover=HOVER,
            border=EDGE,
            size=9,
            padx=12,
            pady=5,
        )
        self.folder_pill.pack(side="left")

        self.counter = tk.Label(top, bg=PANEL, fg=MUTED, font=(FONT, 9, "bold"))
        self.counter.pack(side="left", padx=(12, 0))

        close_btn = tk.Label(
            top, text="✕", bg=PANEL, fg=MUTED, font=(FONT, 11, "bold"), cursor="hand2"
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self.close())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=FG))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=MUTED))

        self.mode = tk.Label(top, bg=PANEL, fg=MUTED, font=(FONT, 9))
        self.mode.pack(side="right", padx=(0, 14))

        self.bar = tk.Canvas(main_frame, height=2, bg=LINE, highlightthickness=0)
        self.bar.pack(fill="x", padx=28)

        # ── 1) Contenitore vista ripasso ──
        self.view_container = tk.Frame(main_frame, bg=PANEL)
        self.view_container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            self.view_container, width=self.CW, height=self.CH, bg=PANEL, highlightthickness=0
        )
        self.canvas.pack(pady=(16, 6), padx=28)

        # Pillola cartella sulla card (per spostare la card corrente al volo)
        self.card_folder_pill = Btn(
            self.canvas,
            "",
            self._open_card_folder_menu,
            fg=ACCENT,
            bg=PILL,
            hover=HOVER,
            border=LINE,
            size=8,
            padx=8,
            pady=2,
        )

        self.text_wrap = tk.Frame(self.canvas, bg=PANEL2)
        self.card_text = tk.Text(
            self.text_wrap,
            bg=PANEL2,
            fg=FG,
            wrap="word",
            bd=0,
            highlightthickness=0,
            relief="flat",
            cursor="hand2",
            takefocus=0,
            exportselection=False,
            padx=8,
            pady=4,
            spacing1=1,
            spacing3=1,
            yscrollcommand=self._on_yscroll,
        )
        self.card_text.tag_configure("center", justify="center")
        self.card_text.pack(side="left", fill="both", expand=True)

        self.sbar = tk.Canvas(self.text_wrap, width=4, bg=PANEL2, highlightthickness=0)
        self.sbar.pack(side="right", fill="y", padx=(4, 0))

        self.hint = tk.Label(self.view_container, bg=PANEL, fg=MUTED, font=(FONT, 10))
        self.hint.pack()

        self.actions = tk.Frame(self.view_container, bg=PANEL, height=48)
        self.actions.pack(fill="x", padx=28, pady=(8, 12))
        self.actions.pack_propagate(False)
        self.grade_btns = tk.Frame(self.actions, bg=PANEL)
        Btn(self.grade_btns, "Non la so · 1", lambda: self.grade(0), fg=BAD, bg=PILL).pack(
            side="left", expand=True, fill="x", padx=4
        )
        Btn(self.grade_btns, "Quasi · 2", lambda: self.grade(1), fg=ACCENT, bg=PILL).pack(
            side="left", expand=True, fill="x", padx=4
        )
        Btn(self.grade_btns, "La so · 3", lambda: self.grade(2), fg=GOOD, bg=PILL).pack(
            side="left", expand=True, fill="x", padx=4
        )

        foot = tk.Frame(self.view_container, bg=PANEL)
        foot.pack(side="bottom", fill="x", padx=28, pady=(0, 16))
        Btn(
            foot,
            "Ripassa tutto",
            lambda: self.start(True),
            fg=MUTED,
            bg=PANEL,
            border=None,
            size=9,
        ).pack(side="left")
        Btn(
            foot,
            "Elimina",
            self.delete_current,
            fg=BAD,
            bg=PANEL,
            border=None,
            size=9,
        ).pack(side="right")
        Btn(
            foot,
            "Modifica · E",
            self.start_edit,
            fg=ACCENT,
            bg=PANEL,
            border=None,
            size=9,
        ).pack(side="right", padx=(0, 8))

        # ── 2) Contenitore libreria Cartelle (stile App Library) ──
        self.folders_container = tk.Frame(main_frame, bg=PANEL)

        f_bar = tk.Frame(
            self.folders_container,
            bg=PANEL2,
            highlightthickness=1,
            highlightbackground=EDGE,
        )
        f_bar.pack(fill="x", padx=28, pady=(16, 12))
        tk.Label(
            f_bar, text="+", bg=PANEL2, fg=MUTED, font=(FONT, 12, "bold"), padx=10
        ).pack(side="left")
        self.new_folder_entry = tk.Entry(
            f_bar,
            bg=PANEL2,
            fg=FG,
            insertbackground=ACCENT,
            font=(FONT, 10),
            bd=0,
            highlightthickness=0,
        )
        self.new_folder_entry.pack(side="left", fill="x", expand=True, pady=8)
        self.new_folder_entry.bind("<Return>", lambda e: self._create_folder())
        Btn(
            f_bar,
            "Crea cartella",
            self._create_folder,
            fg=GOOD,
            bg=PILL,
            border=LINE,
            size=9,
            padx=12,
            pady=4,
        ).pack(side="right", padx=6, pady=4)

        self.folders_canvas = tk.Canvas(
            self.folders_container, bg=PANEL, highlightthickness=0
        )
        self.folders_canvas.pack(fill="both", expand=True, padx=28, pady=(0, 8))
        self.folders_canvas.bind("<MouseWheel>", self._on_folders_wheel)
        self.folders_canvas.bind(
            "<Button-4>", lambda e: self.folders_canvas.yview_scroll(-2, "units")
        )
        self.folders_canvas.bind(
            "<Button-5>", lambda e: self.folders_canvas.yview_scroll(2, "units")
        )

        Btn(
            self.folders_container,
            "Torna al ripasso · Esc",
            self.close_folders_view,
            fg=MUTED,
            bg=PILL,
            size=9,
        ).pack(pady=(4, 16))

        # ── 3) Contenitore modifica flashcard ──
        self.edit_container = tk.Frame(main_frame, bg=PANEL)

        e_top = tk.Frame(self.edit_container, bg=PANEL)
        e_top.pack(fill="x", pady=(2, 8))
        tk.Label(
            e_top, text="Cartella:", bg=PANEL, fg=MUTED, font=(FONT, 9)
        ).pack(side="left")
        self.edit_folder_btn = Btn(
            e_top,
            "",
            self._open_edit_folder_menu,
            fg=ACCENT,
            bg=PILL,
            size=9,
            padx=10,
            pady=3,
        )
        self.edit_folder_btn.pack(side="left", padx=(8, 0))

        tk.Label(
            self.edit_container,
            text="F R O N T E",
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 8, "bold"),
        ).pack(anchor="w", pady=(2, 4))
        f_wrap = tk.Frame(
            self.edit_container, bg=PANEL2, highlightthickness=1, highlightbackground=EDGE
        )
        f_wrap.pack(fill="both", expand=True, pady=(0, 10))
        self.edit_front = tk.Text(
            f_wrap,
            bg=PANEL2,
            fg=FG,
            insertbackground=ACCENT,
            font=(FONT, 11),
            wrap="word",
            bd=0,
            highlightthickness=0,
            height=5,
            padx=12,
            pady=8,
        )
        self.edit_front.pack(fill="both", expand=True)

        tk.Label(
            self.edit_container,
            text="R E T R O",
            bg=PANEL,
            fg=ACCENT,
            font=(FONT, 8, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        b_wrap = tk.Frame(
            self.edit_container, bg=PANEL2, highlightthickness=1, highlightbackground=EDGE
        )
        b_wrap.pack(fill="both", expand=True, pady=(0, 10))
        self.edit_back = tk.Text(
            b_wrap,
            bg=PANEL2,
            fg=FG,
            insertbackground=ACCENT,
            font=(FONT, 11),
            wrap="word",
            bd=0,
            highlightthickness=0,
            height=6,
            padx=12,
            pady=8,
        )
        self.edit_back.pack(fill="both", expand=True)

        self.edit_msg = tk.Label(
            self.edit_container, text="", bg=PANEL, fg=BAD, font=(FONT, 9)
        )
        self.edit_msg.pack(anchor="w", pady=(0, 6))

        e_btns = tk.Frame(self.edit_container, bg=PANEL)
        e_btns.pack(fill="x", pady=(0, 6))
        Btn(e_btns, "Annulla · Esc", self.cancel_edit, fg=MUTED, bg=PILL).pack(
            side="left"
        )
        Btn(
            e_btns, "Salva modifiche · Ctrl+Invio", self.save_edit, fg=GOOD, bg=PILL
        ).pack(side="right", expand=True, fill="x", padx=(10, 0))

        self.edit_front.bind("<Tab>", self._focus_back_field)
        for eb in (self.edit_front, self.edit_back):
            eb.bind("<Control-Return>", lambda e: self._save_shortcut())
            eb.bind("<Command-Return>", lambda e: self._save_shortcut())

        # Scorciatoie globali della finestra
        self.bind("<space>", lambda e: self.flip())
        self.bind("<Return>", lambda e: self.flip())
        self.bind("1", lambda e: self.grade(0))
        self.bind("2", lambda e: self.grade(1))
        self.bind("3", lambda e: self.grade(2))
        self.bind("e", lambda e: self.start_edit())
        self.bind("E", lambda e: self.start_edit())
        self.bind("<Delete>", lambda e: self.delete_current())
        self.bind("<Escape>", self._on_escape)
        self.bind("<Up>", lambda e: self._scroll(-2))
        self.bind("<Down>", lambda e: self._scroll(2))

        for w in (self.canvas, self.text_wrap, self.card_text):
            w.bind("<Button-1>", lambda e: self.flip())
            w.bind("<MouseWheel>", self._on_wheel)
            w.bind("<Button-4>", lambda e: self._scroll(-2))
            w.bind("<Button-5>", lambda e: self._scroll(2))

        for el in (main_frame, top, self.mode, self.counter):
            el.bind("<ButtonPress-1>", self._on_press)
            el.bind("<B1-Motion>", self._on_motion)
            el.bind("<ButtonRelease-1>", lambda e: setattr(self, "_press", None))

        self.start(False)
        self.lift()
        self.focus_force()
        self.after(40, self._refit_current_text)

    # ── Helpers eventi & scroll ──
    def _focus_back_field(self, _e):
        self.edit_back.focus_set()
        return "break"

    def _save_shortcut(self):
        self.save_edit()
        return "break"

    def _on_escape(self, _e):
        if self.editing:
            self.cancel_edit()
        elif self.browsing_folders:
            self.close_folders_view()
        else:
            self.close()

    def _on_yscroll(self, first, last):
        f, l = float(first), float(last)
        self.sbar.delete("all")
        if f <= 0.0 and l >= 1.0:
            return
        h = self.sbar.winfo_height()
        if h > 1:
            self.sbar.create_rectangle(0, f * h, 4, l * h, fill=EDGE, outline="")

    def _scroll(self, lines):
        if (
            not self.editing
            and not self.browsing_folders
            and self.current is not None
            and not self.busy
        ):
            self.card_text.yview_scroll(lines, "units")

    def _on_wheel(self, e):
        if e.delta:
            self._scroll(-2 if e.delta > 0 else 2)
        return "break"

    def _on_folders_wheel(self, e):
        if e.delta:
            self.folders_canvas.yview_scroll(-2 if e.delta > 0 else 2, "units")
        return "break"

    def _on_press(self, e):
        self._press = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())

    def _on_motion(self, e):
        if not self._press:
            return
        dx, dy = e.x_root - self._press[0], e.y_root - self._press[1]
        self.geometry(f"+{self._press[2] + dx}+{self._press[3] + dy}")

    # ── Libreria Cartelle (stile App Library) ──
    def toggle_folders_view(self):
        if self.editing:
            return
        if self.browsing_folders:
            self.close_folders_view()
        else:
            self.open_folders_view()

    def open_folders_view(self):
        self.browsing_folders = True
        self.view_container.pack_forget()
        self.folders_container.pack(fill="both", expand=True)
        self.mode.configure(text="Libreria cartelle")
        self._render_folders_grid()
        self.new_folder_entry.delete(0, "end")
        self.new_folder_entry.focus_set()

    def close_folders_view(self):
        if not self.browsing_folders:
            return
        self.browsing_folders = False
        self.folders_container.pack_forget()
        self.view_container.pack(fill="both", expand=True)
        self.mode.configure(
            text="Tutte le carte" if self._everything else "Da ripassare"
        )
        self.focus_force()
        self.after(30, self._refit_current_text)

    def _create_folder(self):
        name = self.store.add_folder(self.new_folder_entry.get())
        self.new_folder_entry.delete(0, "end")
        if name:
            self._render_folders_grid()
            self.app.widget.refresh()

    def _select_folder_from_grid(self, name):
        self.store.set_active_folder(name)
        self.folder_pill.configure(text=f"📁 {self.store.active_folder} ▾")
        self.close_folders_view()
        self.start(self._everything)

    def _delete_folder_from_grid(self, name):
        self.store.delete_folder(name)
        self.folder_pill.configure(text=f"📁 {self.store.active_folder} ▾")
        self._render_folders_grid()
        self.app.widget.refresh()

    def _render_folders_grid(self):
        c = self.folders_canvas
        c.delete("all")
        items = [ALL_FOLDERS] + list(self.store.folders)
        act = self.store.active_folder

        col_w = 272
        row_h = 116
        gap_x = 18
        gap_y = 14

        for idx, name in enumerate(items):
            col = idx % 2
            row = idx // 2
            x1 = col * (col_w + gap_x) + 1
            y1 = row * (row_h + gap_y) + 4
            x2 = x1 + col_w
            y2 = y1 + row_h

            is_act = name == act
            fill = "#313643" if is_act else PANEL2
            outline = EDGE_ACTIVE if is_act else EDGE

            tag = f"f_{idx}"
            bg_id = rrect(c, x1, y1, x2, y2, 22, fill=fill, outline=outline, tags=(tag,))

            # Mini-icona squircle 2x2 stile App Library nell'angolo sinistro
            ix, iy = x1 + 16, y1 + 18
            for mr in range(2):
                for mc in range(2):
                    sx = ix + mc * 24
                    sy = iy + mr * 24
                    rrect(
                        c,
                        sx,
                        sy,
                        sx + 20,
                        sy + 20,
                        6,
                        fill="#454c5e" if not is_act else "#56617a",
                        outline="#7c8598",
                        tags=(tag,),
                    )

            total_n = len(self.store.cards_in(name))
            due_n = len(self.store.due(name))
            title = "Tutte le cartelle" if name == ALL_FOLDERS else name
            if len(title) > 16:
                title = title[:15] + "…"

            c.create_text(
                x1 + 78,
                y1 + 32,
                text=title,
                anchor="w",
                fill=FG,
                font=(FONT, 11, "bold"),
                tags=(tag,),
            )
            sub = f"{total_n} {'carta' if total_n == 1 else 'carte'}"
            if due_n:
                sub += f"  ·  ↻{due_n}"
            c.create_text(
                x1 + 78,
                y1 + 56,
                text=sub,
                anchor="w",
                fill=ACCENT if due_n else MUTED,
                font=(FONT, 9),
                tags=(tag,),
            )

            if is_act:
                c.create_text(
                    x1 + 78,
                    y1 + 82,
                    text="● Selezionata",
                    anchor="w",
                    fill=GOOD,
                    font=(FONT, 8, "bold"),
                    tags=(tag,),
                )

            c.tag_bind(tag, "<Button-1>", lambda e, n=name: self._select_folder_from_grid(n))
            c.tag_bind(
                tag,
                "<Enter>",
                lambda e, bid=bg_id: c.itemconfigure(bid, fill="#383e4d", outline=EDGE_ACTIVE),
            )
            c.tag_bind(
                tag,
                "<Leave>",
                lambda e, bid=bg_id, f=fill, o=outline: c.itemconfigure(
                    bid, fill=f, outline=o
                ),
            )

            if name not in (ALL_FOLDERS, DEFAULT_FOLDER):
                dtag = f"del_{idx}"
                did = c.create_text(
                    x2 - 16,
                    y1 + 18,
                    text="✕",
                    fill=MUTED,
                    font=(FONT, 9, "bold"),
                    tags=(dtag,),
                )
                c.tag_bind(
                    dtag,
                    "<Button-1>",
                    lambda e, n=name: self._delete_folder_from_grid(n),
                )
                c.tag_bind(dtag, "<Enter>", lambda e, i=did: c.itemconfigure(i, fill=BAD))
                c.tag_bind(dtag, "<Leave>", lambda e, i=did: c.itemconfigure(i, fill=MUTED))

        rows = (len(items) + 1) // 2
        total_h = max(320, rows * (row_h + gap_y) + 8)
        c.configure(scrollregion=(0, 0, 564, total_h))

    def refresh_folder_change(self):
        self.folder_pill.configure(text=f"📁 {self.store.active_folder} ▾")
        if self.browsing_folders:
            self._render_folders_grid()
        elif not self.editing:
            self.start(self._everything)

    # ── Spostamento card singola in cartella ──
    def _open_card_folder_menu(self):
        if self.current is None or self.editing or self.busy:
            return
        cur_f = self.current.get("folder", DEFAULT_FOLDER)
        m = tk.Menu(
            self,
            tearoff=0,
            bg=PANEL2,
            fg=FG,
            activebackground=HOVER,
            activeforeground=FG,
            bd=0,
            font=(FONT, 10),
        )
        for f in self.store.folders:
            m.add_command(
                label=("✓ " if f == cur_f else "   ") + f,
                command=lambda f=f: self._move_current_to(f),
            )
        bx = self.card_folder_pill.winfo_rootx()
        by = self.card_folder_pill.winfo_rooty() + self.card_folder_pill.winfo_height() + 2
        try:
            m.tk_popup(bx, by)
        finally:
            m.grab_release()

    def _move_current_to(self, folder):
        if self.current is None:
            return
        self.store.move_card(self.current, folder)
        self.card_folder_pill.configure(text=f"📁 {folder} ▾")
        act = self.store.active_folder
        if act != ALL_FOLDERS and folder != act:
            self.queue = [c for c in self.queue if c["id"] != self.current["id"]]
            self.i = min(self.i, len(self.queue))
            self.next_card()
        self.app.widget.refresh()

    # ── Ripasso & Contenimento Testo ──
    def start(self, everything):
        if self.editing:
            self.cancel_edit()
        self._everything = everything
        cards = list(self.store.cards_in() if everything else self.store.due())
        random.shuffle(cards)
        self.queue, self.i = cards, 0
        self.folder_pill.configure(text=f"📁 {self.store.active_folder} ▾")
        self.mode.configure(text="Tutte le carte" if everything else "Da ripassare")
        self.next_card()

    def next_card(self):
        self.showing_back = False
        self.busy = False
        if self.i >= len(self.queue):
            self.current = None
            if self.store.cards_in():
                self._message(
                    "Tutto ripassato  ✓",
                    "Nessuna carta in scadenza in questa cartella. Premi “Ripassa tutto” o cambia cartella.",
                )
            else:
                self._message(
                    "Nessuna carta qui",
                    "Seleziona del testo in qualsiasi app per creare una flashcard in questa cartella.",
                )
        else:
            self.current = self.queue[self.i]
            self._draw(1.0, False)
        self._sync()

    def _sync(self):
        self.app.widget.refresh()
        total = len(self.queue)
        left = max(0, total - self.i)
        self.counter.configure(text=f"{left} da vedere" if total else "")
        self.bar.update_idletasks()
        w = self.bar.winfo_width()
        self.bar.delete("all")
        if total:
            self.bar.create_rectangle(
                0, 0, w * min(self.i, total) / total, 2, fill=ACCENT, width=0
            )
        if self.current is None:
            self.hint.configure(text="")
            self.grade_btns.pack_forget()
        elif self.showing_back:
            self.hint.configure(text="Quanto bene la sapevi?")
            self.grade_btns.pack(fill="both", expand=True)
        else:
            self.hint.configure(text="Spazio o click sulla carta per girarla")
            self.grade_btns.pack_forget()

    def _pick_initial_font(self, text):
        n = len(text)
        lines = text.count("\n") + 1
        if n < 40 and lines <= 2:
            return 18
        if n < 100 and lines <= 4:
            return 15
        if n < 210 and lines <= 6:
            return 13
        if n < 360 and lines <= 9:
            return 11
        return 10

    def _fit_card_text(self, text):
        box_h = self.CH - 68
        size = self._pick_initial_font(text)

        self.card_text.configure(state="normal", font=(FONT, size), pady=4)
        self.card_text.delete("1.0", "end")
        self.card_text.insert("1.0", text)
        self.card_text.tag_add("center", "1.0", "end")
        self.card_text.configure(state="disabled")
        self.text_wrap.update_idletasks()

        res = self.card_text.count("1.0", "end", "ypixels")
        # Se il testo sfora verticalmente e si può ridurre un po' il font, lo adeguiamo
        while res and res[0] > box_h - 14 and size > 10:
            size = max(10, size - 2)
            self.card_text.configure(font=(FONT, size), pady=4)
            self.text_wrap.update_idletasks()
            res = self.card_text.count("1.0", "end", "ypixels")

        if res and res[0] < box_h - 14:
            self.card_text.configure(pady=max(4, (box_h - res[0]) // 2))
        else:
            self.card_text.configure(pady=4)

        self.card_text.yview_moveto(0.0)
        self.text_wrap.update_idletasks()
        self._on_yscroll(*self.card_text.yview())

    def _refit_current_text(self):
        self._refit_job = None
        if self.current and not self.busy and not self.editing and not self.browsing_folders:
            text = self.current["back" if self.showing_back else "front"]
            self._fit_card_text(text)

    def _draw(self, scale, back):
        c = self.canvas
        c.delete("all")
        W, H = self.CW, self.CH
        cx = W / 2
        hw = max(2, (W / 2 - 4) * scale)
        rrect(c, cx - hw, 4, cx + hw, H - 4, 24, fill=PANEL2, outline=EDGE)

        if scale >= 0.99 and self.current:
            c.create_text(
                28,
                26,
                text="R E T R O" if back else "F R O N T E",
                anchor="w",
                fill=ACCENT if back else MUTED,
                font=(FONT, 8, "bold"),
            )

            cf = self.current.get("folder", DEFAULT_FOLDER)
            self.card_folder_pill.configure(text=f"📁 {cf} ▾")
            c.create_window(W - 22, 26, anchor="e", window=self.card_folder_pill)

            box_w = W - 44
            box_h = H - 68
            c.create_window(
                cx,
                H / 2 + 14,
                width=box_w,
                height=box_h,
                window=self.text_wrap,
            )

            text = self.current["back" if back else "front"]
            self._fit_card_text(text)

    def _message(self, title, sub):
        c = self.canvas
        c.delete("all")
        rrect(c, 4, 4, self.CW - 4, self.CH - 4, 24, fill=PANEL2, outline=EDGE)
        c.create_text(
            self.CW / 2, self.CH / 2 - 16, text=title, fill=FG, font=(FONT, 18, "bold")
        )
        c.create_text(
            self.CW / 2,
            self.CH / 2 + 20,
            text=sub,
            fill=MUTED,
            font=(FONT, 10),
            width=self.CW - 80,
            justify="center",
        )

    # ── Modifica flashcard ──
    def start_edit(self):
        if self.editing or self.browsing_folders or self.current is None or self.busy:
            return
        self.editing = True
        self.edit_msg.configure(text="")
        self._edit_folder = self.current.get("folder", DEFAULT_FOLDER)
        self.edit_folder_btn.configure(text=f"📁 {self._edit_folder} ▾")
        self.edit_front.delete("1.0", "end")
        self.edit_front.insert("1.0", self.current["front"])
        self.edit_back.delete("1.0", "end")
        self.edit_back.insert("1.0", self.current["back"])

        self.view_container.pack_forget()
        self.edit_container.pack(fill="both", expand=True, padx=28, pady=(12, 16))
        self.mode.configure(text="Modifica carta")
        self.edit_front.focus_set()

    def _open_edit_folder_menu(self):
        m = tk.Menu(
            self,
            tearoff=0,
            bg=PANEL2,
            fg=FG,
            activebackground=HOVER,
            activeforeground=FG,
            bd=0,
            font=(FONT, 10),
        )
        for f in self.store.folders:
            m.add_command(
                label=("✓ " if f == self._edit_folder else "   ") + f,
                command=lambda f=f: self._set_edit_folder(f),
            )
        bx = self.edit_folder_btn.winfo_rootx()
        by = self.edit_folder_btn.winfo_rooty() + self.edit_folder_btn.winfo_height() + 2
        try:
            m.tk_popup(bx, by)
        finally:
            m.grab_release()

    def _set_edit_folder(self, name):
        self._edit_folder = name
        self.edit_folder_btn.configure(text=f"📁 {name} ▾")

    def save_edit(self):
        if not self.editing or self.current is None:
            return
        nf = self.edit_front.get("1.0", "end-1c").strip()
        nb = self.edit_back.get("1.0", "end-1c").strip()
        if not nf or not nb:
            self.edit_msg.configure(text="Fronte e Retro non possono essere vuoti.")
            return
        self.store.update(self.current, nf, nb, folder=self._edit_folder)
        self.editing = False
        self.edit_container.pack_forget()
        self.view_container.pack(fill="both", expand=True)
        self.mode.configure(text="Modifica salvata ✓")

        act = self.store.active_folder
        if act != ALL_FOLDERS and self._edit_folder != act:
            self.queue = [c for c in self.queue if c["id"] != self.current["id"]]
            self.i = min(self.i, len(self.queue))
            self.next_card()
        else:
            self._draw(1.0, self.showing_back)
            self._sync()
        self.focus_force()

    def cancel_edit(self):
        if not self.editing:
            return
        self.editing = False
        self.edit_container.pack_forget()
        self.view_container.pack(fill="both", expand=True)
        self.mode.configure(
            text="Tutte le carte" if self._everything else "Da ripassare"
        )
        self.focus_force()
        self.after(20, self._refit_current_text)

    def flip(self):
        if self.editing or self.browsing_folders or self.busy or self.current is None:
            return
        self.busy = True
        shrink = [0.85, 0.6, 0.35, 0.12]
        n = len(shrink)

        def step(k):
            if k < n:
                self._draw(shrink[k], self.showing_back)
                self.after(12, step, k + 1)
            elif k == n:
                self.showing_back = not self.showing_back
                self.after(12, step, k + 1)
            elif k <= 2 * n:
                self._draw(shrink[2 * n - k], self.showing_back)
                self.after(12, step, k + 1)
            else:
                self._draw(1.0, self.showing_back)
                self.busy = False
                self._sync()

        step(0)

    def grade(self, level):
        if (
            self.editing
            or self.browsing_folders
            or self.current is None
            or not self.showing_back
            or self.busy
        ):
            return
        self.store.grade(self.current, level)
        if level == 0:
            self.queue.append(self.current)
        self.i += 1
        self.next_card()

    def delete_current(self):
        if self.editing or self.browsing_folders or self.current is None:
            return
        card = self.current
        self.store.delete(card)
        self.queue = [c for c in self.queue if c["id"] != card["id"]]
        self.i = min(self.i, len(self.queue))
        self.next_card()

    def close(self):
        self.app.review = None
        self.destroy()


# ───────────────────────── Cattura del testo selezionato ─────────────────────────
class Grabber:
    def __init__(self, app):
        self.app = app
        self.kb = keyboard.Controller()
        self.lock = threading.Lock()
        self.mod = keyboard.Key.cmd if IS_MAC else keyboard.Key.ctrl

    def grab(self, x, y):
        if not self.lock.acquire(blocking=False):
            return
        try:
            try:
                old = pyperclip.paste()
            except Exception:
                old = None
            marker = f"__flashsnap_{time.time_ns()}__"
            pyperclip.copy(marker)
            time.sleep(0.025)
            with self.kb.pressed(self.mod):
                self.kb.press("c")
                self.kb.release("c")
            text = None
            for _ in range(10):
                time.sleep(0.03)
                cur = pyperclip.paste()
                if cur != marker:
                    text = cur
                    break
            try:
                if old is not None:
                    pyperclip.copy(old)
            except Exception:
                pass
            if text and text.strip():
                self.app.q.put(("text", text.strip()[:4000], x, y))
        except Exception:
            pass
        finally:
            self.lock.release()


# ───────────────────────── App ─────────────────────────
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.store = Store()
        self.q = queue.Queue()
        self.pending = None  # ("front"|"back", testo)
        self.paused = False
        self.review = None
        self._grab_job = None
        self._hide_job = None
        self._down = None
        self._last_up = (0, 0, 0.0)
        self._active_until = 0.0

        self.widget = Widget(self)
        self.popup = Popup(self)
        self.grabber = Grabber(self)

        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.mouse_listener.daemon = True
        self.mouse_listener.start()
        self.hotkeys = None
        try:
            combo = "<cmd>+<alt>+p" if IS_MAC else "<ctrl>+<alt>+p"
            self.hotkeys = keyboard.GlobalHotKeys(
                {combo: lambda: self.q.put(("toggle",))}
            )
            self.hotkeys.daemon = True
            self.hotkeys.start()
        except Exception:
            pass
        self.root.after(120, self._poll)

    def _on_click(self, x, y, button, pressed):
        if button != mouse.Button.left:
            return
        if self.paused and not self.popup.visible:
            return
        now = time.monotonic()
        if pressed:
            self._down = (x, y)
            if self.popup.visible:
                self.q.put(("down", x, y))
        elif self._down:
            sx, sy = self._down
            drag = math.hypot(x - sx, y - sy) > 8
            lx, ly, lt = self._last_up
            dbl = (now - lt < 0.4) and math.hypot(x - lx, y - ly) < 6
            self._last_up = (x, y, now)
            if drag or dbl:
                self.q.put(("sel", sx, sy, x, y))

    def _inside_own(self, x, y, pad=6):
        wins = [self.widget]
        if self.popup.visible:
            wins.append(self.popup)
        if self.review is not None:
            wins.append(self.review)
        for w in wins:
            try:
                if w.winfo_ismapped():
                    wx, wy = w.winfo_rootx(), w.winfo_rooty()
                    ww = max(w.winfo_width(), w.winfo_reqwidth())
                    wh = max(w.winfo_height(), w.winfo_reqheight())
                    if wx - pad <= x <= wx + ww + pad and wy - pad <= y <= wy + wh + pad:
                        return True
            except tk.TclError:
                pass
        return False

    def _maybe_hide_popup(self):
        self._hide_job = None
        if (
            not self.popup.menu_open
            and time.monotonic() - self.popup._last_pick > 0.25
            and not self.popup.has_unsaved_input()
        ):
            self.popup.hide()

    def _poll(self):
        got_event = False
        try:
            while True:
                ev = self.q.get_nowait()
                got_event = True
                kind = ev[0]
                if kind == "down":
                    if (
                        self.popup.visible
                        and not self.popup.menu_open
                        and not self._inside_own(ev[1], ev[2])
                    ):
                        if self._hide_job:
                            self.root.after_cancel(self._hide_job)
                        self._hide_job = self.root.after(70, self._maybe_hide_popup)
                elif kind == "sel":
                    _, sx, sy, x, y = ev
                    if self.paused or self._inside_own(sx, sy) or self._inside_own(x, y):
                        continue
                    if self._grab_job:
                        self.root.after_cancel(self._grab_job)
                    self._grab_job = self.root.after(
                        130,
                        lambda x=x, y=y: threading.Thread(
                            target=self.grabber.grab, args=(x, y), daemon=True
                        ).start(),
                    )
                elif kind == "text":
                    if self._hide_job:
                        self.root.after_cancel(self._hide_job)
                        self._hide_job = None
                    self.popup.show(ev[1], ev[2], ev[3])
                elif kind == "toggle":
                    self.toggle_pause()
        except queue.Empty:
            pass

        now = time.monotonic()
        if got_event:
            self._active_until = now + 1.5
        delay = 30 if now < self._active_until else 160
        self.root.after(delay, self._poll)

    def choose(self, side, text, folder=None, open_writer=False):
        if self.pending:
            if self.pending[0] != side:
                front = text if side == "front" else self.pending[1]
                back = text if side == "back" else self.pending[1]
                self.store.add(front, back, folder=folder)
                self.pending = None
                self.popup.hide()
                self.widget.flash(GOOD)
            else:
                self.pending = (side, text)
                self.widget.flash(MUTED)
                if open_writer:
                    self.popup._enter_write_mode(side)
                else:
                    self.popup.hide()
        else:
            self.pending = (side, text)
            self.widget.flash(MUTED)
            if open_writer:
                self.popup._enter_write_mode(side)
            else:
                self.popup.hide()

        self.widget.refresh()
        if self.review and not self.review.editing and not self.review.browsing_folders:
            self.review._sync()

    def cancel_pending(self):
        self.pending = None
        self.widget.refresh()

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.popup.hide()
        self.widget.refresh()

    def open_review(self):
        if self.review:
            try:
                self.review.lift()
                self.review.focus_force()
                return
            except tk.TclError:
                self.review = None
        self.popup.hide()
        self.review = Review(self)

    def quit(self):
        for listener in (self.mouse_listener, self.hotkeys):
            if listener:
                try:
                    listener.stop()
                except Exception:
                    pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()