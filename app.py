import customtkinter as ctk
import io
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
import math
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
import darkdetect
from PIL import Image
import pymupdf
from tkinterdnd2 import TkinterDnD, DND_FILES

# Import our backend
import pdf_tools
import settings
from pdf_tools import format_size

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp"]
SINGLE_FILE_TOOLS = ["Girar PDF", "Cortar PDF"]
OUTPUT_FILE_TOOLS = ["Juntar PDFs", "Imagens para PDF"]
DROP_HIGHLIGHT = ("#3B8ED0", "#1F6AA5")
LIST_BG = ("#EBEBEB", "#2B2B2B")
DANGER = ("#D9534F", "#C9302C")
DANGER_HOVER = ("#C9302C", "#A52A2A")

def parse_number(text, field_name, default):
    """Accepts both '2.5' and '2,5'; raises ValueError with a user-facing message."""
    text = text.strip().replace(",", ".")
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"Valor inválido em '{field_name}': {text}")
    if not math.isfinite(value): # float() also accepts "nan" and "inf"
        raise ValueError(f"Valor inválido em '{field_name}': {text}")
    if value < 0:
        raise ValueError(f"'{field_name}' não pode ser negativo.")
    return value

def format_number(value):
    return f"{value:g}".replace(".", ",")

def shorten_path(path, max_chars=60):
    """'C:\\Users\\...\\docs\\pasta' style: keeps the drive and the last folders readable."""
    path = str(path)
    if len(path) <= max_chars:
        return path
    parts = Path(path).parts
    tail = parts[-1]
    for part in reversed(parts[1:-1]):
        if len(parts[0]) + len(part) + len(tail) + 5 > max_chars:
            break
        tail = str(Path(part) / tail)
    return str(Path(parts[0]) / "…" / tail)

def natural_key(text):
    """Sort key where 'pagina2' comes before 'pagina10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]

def open_in_explorer(path):
    """Opens a folder, or the folder containing a file with the file selected."""
    path = Path(path)
    try:
        if sys.platform == "win32":
            if path.is_file():
                subprocess.Popen(f'explorer /select,"{path}"')
            else:
                os.startfile(str(path))
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path if path.is_dir() else path.parent)])
    except OSError as e:
        messagebox.showerror("Erro", f"Não foi possível abrir a pasta:\n\n{e}")

def apply_treeview_style(root):
    """ttk widgets don't follow CustomTkinter's theme, so the file table is styled by hand."""
    dark = ctk.get_appearance_mode() == "Dark"
    bg = LIST_BG[1] if dark else LIST_BG[0]
    fg, head_bg, selected = ("#DCE4EE", "#333333", "#1F6AA5") if dark else ("#1A1A1A", "#D6D6D6", "#3B8ED0")
    if not hasattr(root, "_tree_fonts"):
        root._tree_fonts = (tkfont.Font(root=root, family="Segoe UI", size=10),
                            tkfont.Font(root=root, family="Segoe UI", size=10, weight="bold"))
    font, head_font = root._tree_fonts
    style = ttk.Style(root)
    style.theme_use("default") # the native Windows theme ignores custom colors
    style.configure("Files.Treeview", background=bg, fieldbackground=bg, foreground=fg, font=font,
                    rowheight=font.metrics("linespace") + 10, borderwidth=0)
    style.map("Files.Treeview", background=[("selected", selected)], foreground=[("selected", "#FFFFFF")])
    style.configure("Files.Treeview.Heading", background=head_bg, foreground=fg, font=head_font, relief="flat", padding=(6, 4))
    style.map("Files.Treeview.Heading", background=[("active", head_bg)])
    style.layout("Files.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

class FileList(ctk.CTkFrame):
    """Table of input files showing pages (or image size) and file size, with drag-to-reorder,
    move up/down, sort and remove."""

    def __init__(self, master, app, is_image_list, single_file, on_change):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.is_image_list = is_image_list
        self.single_file = single_file
        self.on_change = on_change
        self.info = {} # path -> dict(label, size, valid, pages)
        self.drag_item = None
        self.drag_moved = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tree_frame = ctk.CTkFrame(self, fg_color=LIST_BG, border_width=2, border_color=LIST_BG)
        self.tree_frame.grid(row=0, column=0, sticky="nsew")
        self.tree_frame.grid_columnconfigure(0, weight=1)
        self.tree_frame.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(self.tree_frame, columns=("name", "info", "size"), show="headings",
                                 selectmode="browse" if single_file else "extended", style="Files.Treeview")
        self.tree.heading("name", text="Arquivo", anchor="w")
        self.tree.heading("info", text="Dimensões" if is_image_list else "Páginas")
        self.tree.heading("size", text="Tamanho", anchor="e")
        self.tree.column("name", anchor="w", stretch=True, width=300)
        self.tree.column("info", anchor="center", stretch=False, width=110)
        self.tree.column("size", anchor="e", stretch=False, width=90)
        self.tree.tag_configure("invalid", foreground="#E5534B")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)

        scrollbar = ctk.CTkScrollbar(self.tree_frame, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=4)
        self.tree.configure(yscrollcommand=scrollbar.set)

        hint = "Arraste um arquivo para cá" if single_file else "Arraste arquivos ou pastas para cá"
        self.lbl_empty = ctk.CTkLabel(self.tree_frame, text=f"{hint}\nou use os botões acima", text_color="gray", fg_color=LIST_BG)

        # Actions row
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        if not single_file:
            small = dict(width=34, height=28)
            ctk.CTkButton(actions, text="▲", command=lambda: self.move_selected(-1), **small).pack(side="left", padx=(0, 4))
            ctk.CTkButton(actions, text="▼", command=lambda: self.move_selected(1), **small).pack(side="left", padx=(0, 4))
            ctk.CTkButton(actions, text="Ordenar A–Z", width=100, height=28, command=self.sort_by_name).pack(side="left", padx=(0, 4))
            ctk.CTkButton(actions, text="Remover", width=80, height=28, command=self.remove_selected).pack(side="left")
        self.lbl_footer = ctk.CTkLabel(actions, text="", text_color="gray")
        self.lbl_footer.pack(side="right")

        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<BackSpace>", lambda e: self.remove_selected())
        self.tree.bind("<Control-a>", lambda e: self.tree.selection_set(self.tree.get_children()))
        self.tree.bind("<Double-1>", self.on_double_click)
        if not single_file:
            self.tree.bind("<ButtonPress-1>", self.on_press, add="+")
            self.tree.bind("<B1-Motion>", self.on_motion, add="+")
            self.tree.bind("<ButtonRelease-1>", self.on_release, add="+")
        self.refresh()

    @property
    def files(self):
        return list(self.tree.get_children())

    def add(self, paths):
        if self.single_file:
            self.tree.delete(*self.tree.get_children())
            paths = paths[:1]
        existing = {os.path.normcase(f) for f in self.files}
        new = []
        for p in paths:
            p = str(Path(p))
            if os.path.normcase(p) in existing:
                continue
            existing.add(os.path.normcase(p))
            cached = self.info.get(p)
            self.tree.insert("", "end", iid=p, values=(Path(p).name, "…", ""))
            if cached:
                self.show_info(p)
            else:
                new.append(p)
        self.refresh()
        self.load_info(new)
        self.on_change()

    def clear(self):
        self.tree.delete(*self.tree.get_children())
        self.refresh()
        self.on_change()

    def remove_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        next_item = self.tree.next(selected[-1]) or self.tree.prev(selected[0])
        self.tree.delete(*selected)
        if next_item and self.tree.exists(next_item):
            self.tree.selection_set(next_item)
        self.refresh()
        self.on_change()

    def move_selected(self, delta):
        selected = set(self.tree.selection())
        if not selected:
            return
        for item in sorted(selected, key=self.tree.index, reverse=delta > 0):
            new_index = self.tree.index(item) + delta
            children = self.tree.get_children()
            # Selected items move as a block: stop at the edges and never jump over each other
            if 0 <= new_index < len(children) and children[new_index] not in selected:
                self.tree.move(item, "", new_index)
        self.tree.see(sorted(selected, key=self.tree.index)[0 if delta < 0 else -1])
        self.on_change()

    def sort_by_name(self):
        for index, item in enumerate(sorted(self.files, key=lambda f: natural_key(Path(f).name))):
            self.tree.move(item, "", index)
        self.on_change()

    def on_press(self, event):
        self.drag_item = self.tree.identify_row(event.y)
        self.drag_moved = False

    def on_motion(self, event):
        target = self.tree.identify_row(event.y)
        if self.drag_item and target and target != self.drag_item:
            self.tree.move(self.drag_item, "", self.tree.index(target))
            self.tree.configure(cursor="sb_v_double_arrow")
            self.drag_moved = True

    def on_release(self, event):
        self.tree.configure(cursor="")
        if self.drag_moved:
            self.tree.selection_set(self.drag_item)
            self.on_change()
        self.drag_item = None
        self.drag_moved = False

    def on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            try:
                os.startfile(item)
            except (OSError, AttributeError):
                pass

    def load_info(self, paths):
        """Reads page counts / image sizes in the background so large batches don't freeze the UI."""
        if not paths:
            return
        def work():
            for p in paths:
                info = self.read_info(p)
                self.app.post(lambda p=p, info=info: self.set_info(p, info))
        threading.Thread(target=work, daemon=True).start()

    def read_info(self, path):
        info = {"label": "inválido", "size": 0, "valid": False, "pages": 0}
        try:
            info["size"] = os.path.getsize(path)
            if self.is_image_list:
                with Image.open(path) as im:
                    w, h = im.size
                    if im.getexif().get(0x0112) in (5, 6, 7, 8): # EXIF orientation swaps width/height
                        w, h = h, w
                info.update(label=f"{w} × {h}", valid=True)
            else:
                with pymupdf.open(path) as doc:
                    if doc.needs_pass:
                        info["label"] = "🔒 senha"
                    else:
                        info.update(label=str(doc.page_count), valid=True, pages=doc.page_count)
        except Exception:
            pass
        return info

    def set_info(self, path, info):
        self.info[path] = info
        self.show_info(path)
        self.refresh()

    def show_info(self, path):
        if not self.tree.exists(path):
            return
        info = self.info[path]
        self.tree.item(path, values=(Path(path).name, info["label"], format_size(info["size"])),
                       tags=() if info["valid"] else ("invalid",))

    def page_count(self, path):
        if path not in self.info:
            self.info[path] = self.read_info(path)
        return self.info[path]["pages"]

    def invalid_files(self):
        return [f for f in self.files if f in self.info and not self.info[f]["valid"]]

    def refresh(self):
        files = self.files
        if files:
            self.lbl_empty.place_forget()
        else:
            self.lbl_empty.place(relx=0.5, rely=0.55, anchor="center")

        if not files:
            self.lbl_footer.configure(text="")
            return
        parts = [f"{len(files)} arquivo" + ("s" if len(files) > 1 else "")]
        known = [self.info[f] for f in files if f in self.info]
        if not self.is_image_list:
            pages = sum(i["pages"] for i in known)
            parts.append(f"{pages} página" + ("s" if pages != 1 else ""))
        parts.append(format_size(sum(i["size"] for i in known)))
        invalid = sum(1 for i in known if not i["valid"])
        if invalid:
            parts.append(f"{invalid} com problema")
        if len(known) < len(files):
            parts.append("lendo…")
        self.lbl_footer.configure(text="  ·  ".join(parts))

    def set_drop_highlight(self, active):
        self.tree_frame.configure(border_color=DROP_HIGHLIGHT if active else LIST_BG)

class ToolView(ctk.CTkFrame):
    def __init__(self, master, app, tool_name, tool_action, extra_options_widget=None, preset_files=None):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.app = app
        self.tool_name = tool_name
        self.tool_action = tool_action
        self.exts = IMAGE_EXTS if tool_name == "Imagens para PDF" else [".pdf"]
        self.single_file = tool_name in SINGLE_FILE_TOOLS
        self.output_key = f"tool:{tool_name}:output_dir"
        self.options_key = f"tool:{tool_name}:options"
        self.cancel_event = None
        self.last_output = None

        saved_dir = settings.get(self.output_key)
        self.output_dir_is_auto = not (saved_dir and Path(saved_dir).is_dir())
        self.output_dir = "" if self.output_dir_is_auto else saved_dir

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        self.lbl_title = ctk.CTkLabel(self, text=self.tool_name, font=ctk.CTkFont(size=24, weight="bold"))
        self.lbl_title.grid(row=0, column=0, pady=(20, 10), padx=20, sticky="w")

        # Files Selection
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=1, column=0, padx=20, sticky="ew")

        self.btn_select_files = ctk.CTkButton(self.btn_frame, text="Selecionar Arquivo" if self.single_file else "Selecionar Arquivos", command=self.select_files)
        self.btn_select_files.pack(side="left", padx=(0, 10))

        if not self.single_file:
            self.btn_select_folder = ctk.CTkButton(self.btn_frame, text="Selecionar Pasta Inteira", command=self.select_folder)
            self.btn_select_folder.pack(side="left")

            self.btn_clear = ctk.CTkButton(self.btn_frame, text="Limpar Lista", width=110, fg_color="transparent",
                                           border_width=1, text_color=("gray10", "gray90"), command=lambda: self.file_list.clear())
            self.btn_clear.pack(side="right")

        self.file_list = FileList(self, app, self.exts == IMAGE_EXTS, self.single_file, on_change=self.update_output_label)
        self.file_list.grid(row=2, column=0, padx=20, pady=10, sticky="nsew")

        # Drag and drop: the whole tool view accepts files and folders
        for widget in (self, self.file_list.tree):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self.on_drop_enter)
            widget.dnd_bind("<<DropLeave>>", self.on_drop_leave)
            widget.dnd_bind("<<Drop>>", self.on_drop)

        # Output Dir
        self.out_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.out_frame.grid(row=3, column=0, padx=20, pady=(0, 5), sticky="ew")

        self.btn_select_out = ctk.CTkButton(self.out_frame, text="Pasta de Destino", command=self.select_out_dir)
        self.btn_select_out.pack(side="left")

        self.btn_reset_out = ctk.CTkButton(self.out_frame, text="↺ Usar pasta do original", width=160, fg_color="transparent",
                                           text_color=("gray10", "gray90"), hover_color=("gray80", "gray30"), command=self.reset_out_dir)

        self.lbl_out_dir = ctk.CTkLabel(self.out_frame, text="", anchor="w")
        self.lbl_out_dir.pack(side="left", padx=10, fill="x", expand=True)

        # Extra options (if any)
        self.extra_options_frame = None
        if extra_options_widget:
            self.extra_options_frame = extra_options_widget(self)
            self.extra_options_frame.grid(row=4, column=0, padx=20, pady=5, sticky="ew")
            saved_options = settings.get(self.options_key)
            if saved_options and hasattr(self.extra_options_frame, "load_values"):
                try:
                    self.extra_options_frame.load_values(saved_options)
                except Exception:
                    pass # stale or hand-edited settings: keep the defaults

        # Process Button
        self.process_text = "Abrir Editor Visual" if self.single_file else "Processar"
        self.btn_process = ctk.CTkButton(self, text=self.process_text, font=ctk.CTkFont(size=16, weight="bold"), height=40, width=180, command=self.process)
        self.btn_process.grid(row=5, column=0, pady=10)
        self.process_colors = (self.btn_process.cget("fg_color"), self.btn_process.cget("hover_color"))

        # Progress
        if not self.single_file:
            self.progressbar = ctk.CTkProgressBar(self)
            self.progressbar.grid(row=6, column=0, padx=20, pady=5, sticky="ew")
            self.progressbar.set(0)

        self.status_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.status_frame.grid(row=7, column=0, padx=20, pady=(0, 15), sticky="ew")
        self.status_frame.grid_columnconfigure(0, weight=1)

        self.lbl_status = ctk.CTkLabel(self.status_frame, text="" if self.single_file else "Aguardando...", anchor="w")
        self.lbl_status.grid(row=0, column=0, sticky="ew")

        self.btn_open_output = ctk.CTkButton(self.status_frame, text="📂 Abrir pasta de destino", width=180,
                                             command=lambda: open_in_explorer(self.last_output))

        if preset_files:
            self.file_list.add(preset_files)
        self.update_output_label()

    def select_files(self):
        patterns = " ".join(f"*{ext}" for ext in self.exts)
        label = "Imagens" if self.exts == IMAGE_EXTS else "Arquivos PDF"
        kwargs = dict(title="Selecione os arquivos", filetypes=[(label, patterns)], initialdir=settings.get("last_input_dir"))
        files = [filedialog.askopenfilename(**kwargs)] if self.single_file else filedialog.askopenfilenames(**kwargs)
        files = [f for f in files if f]
        if files:
            settings.set("last_input_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def select_folder(self):
        folder = filedialog.askdirectory(title="Selecione uma pasta de entrada", initialdir=settings.get("last_input_dir"))
        if folder:
            settings.set("last_input_dir", folder)
            self.add_paths([folder])

    def add_paths(self, paths):
        """Adds files and/or folders (searched recursively), keeping only compatible extensions."""
        new_files = []
        ignored = 0
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                found = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in self.exts]
                new_files.extend(str(p) for p in sorted(found, key=lambda p: natural_key(str(p))))
            elif path.is_file() and path.suffix.lower() in self.exts:
                new_files.append(str(path))
            else:
                ignored += 1

        if not new_files:
            messagebox.showinfo("Aviso", f"Nenhum arquivo compatível encontrado ({', '.join(self.exts)}).")
            return

        if self.single_file and len(new_files) > 1:
            messagebox.showinfo("Aviso", f"Esta ferramenta edita um arquivo por vez. Usando: {Path(new_files[0]).name}")
        self.file_list.add(new_files)

        if ignored:
            self.lbl_status.configure(text=f"{ignored} item(ns) ignorado(s) por não ser(em) {', '.join(self.exts)}")

    def on_drop_enter(self, event):
        self.file_list.set_drop_highlight(True)
        return event.action

    def on_drop_leave(self, event):
        self.file_list.set_drop_highlight(False)
        return event.action

    def on_drop(self, event):
        self.file_list.set_drop_highlight(False)
        # splitlist handles Tcl's {braces} around paths that contain spaces
        self.add_paths(self.tk.splitlist(event.data))
        return event.action

    def update_output_label(self):
        if self.output_dir_is_auto:
            files = self.file_list.files
            self.output_dir = str(Path(files[0]).parent) if files else ""
            text = f"Destino: {shorten_path(self.output_dir)}  (pasta do 1º arquivo)" if files else "Destino: mesma pasta do primeiro arquivo"
            self.btn_reset_out.pack_forget()
        else:
            text = f"Destino: {shorten_path(self.output_dir)}"
            self.btn_reset_out.pack(side="left", padx=(10, 0), before=self.lbl_out_dir)
        self.lbl_out_dir.configure(text=text)

    def select_out_dir(self):
        folder = filedialog.askdirectory(title="Selecione a pasta de destino", initialdir=self.output_dir or None)
        if folder:
            self.output_dir = folder
            self.output_dir_is_auto = False
            settings.set(self.output_key, folder)
            self.update_output_label()

    def reset_out_dir(self):
        self.output_dir_is_auto = True
        settings.set(self.output_key, None)
        self.update_output_label()

    def update_progress(self, current, total, message):
        # Called from the worker thread: hand the UI update to the main thread
        def update():
            self.progressbar.set(current / total if total > 0 else 0)
            self.lbl_status.configure(text=message)
        self.app.post(update)

    @property
    def is_running(self):
        return self.cancel_event is not None

    def confirm_leave(self):
        if not self.is_running:
            return True
        if messagebox.askyesno("Processamento em andamento", "Um processamento ainda está em andamento.\n\nDeseja cancelá-lo e sair?"):
            self.cancel_event.set()
            return True
        return False

    def process(self):
        files = self.file_list.files
        if not files:
            messagebox.showwarning("Aviso", "Por favor, selecione os arquivos primeiro.")
            return

        invalid = self.file_list.invalid_files()
        if invalid:
            names = "\n".join(f"• {Path(f).name}" for f in invalid[:10]) + ("\n…" if len(invalid) > 10 else "")
            if len(invalid) == len(files):
                messagebox.showerror("Arquivos com problema", f"Nenhum dos arquivos pode ser lido (inválido ou protegido por senha):\n\n{names}")
                return
            if not messagebox.askyesno("Arquivos com problema", f"Estes arquivos não podem ser lidos (inválidos ou protegidos por senha) e serão ignorados:\n\n{names}\n\nDeseja continuar com os demais?"):
                return
            files = [f for f in files if f not in invalid]

        if self.single_file:
            self.app.open_visual_editor(self.tool_name, files[0], self.output_dir)
            return

        # Gather extra kwargs
        kwargs = {}
        if hasattr(self.extra_options_frame, "get_values"):
            try:
                kwargs = self.extra_options_frame.get_values()
            except ValueError as e:
                messagebox.showerror("Valor Inválido", str(e))
                return
            if hasattr(self.extra_options_frame, "load_values"):
                settings.set(self.options_key, dict(kwargs))

        out_path = self.output_dir
        if self.tool_name in OUTPUT_FILE_TOOLS:
            out_path = Path(self.output_dir) / kwargs.pop("output_filename")
            if any(Path(f).resolve() == out_path.resolve() for f in files):
                messagebox.showerror("Nome Inválido", f"'{out_path.name}' é um dos arquivos de entrada. Escolha outro nome ou outra pasta de destino.")
                return
            if out_path.exists() and not messagebox.askyesno("Arquivo Existente", f"'{out_path.name}' já existe na pasta de destino.\n\nDeseja substituí-lo?"):
                return

        # Pre-flight check for Adicionar Margens
        if self.tool_name == "Adicionar Margens":
            out_dir = Path(self.output_dir).resolve()
            if any(Path(f).resolve().parent == out_dir for f in files):
                messagebox.showerror(
                    "Pasta Inválida",
                    "Para evitar qualquer risco de sobrescrever os originais, a pasta de destino não pode ser a mesma pasta de onde os PDFs vieram.\n\nPor favor, selecione uma pasta de saída diferente."
                )
                return

            limit = pdf_tools.MARGINS_MAX_PAGES
            too_long = [(f, self.file_list.page_count(f)) for f in files]
            too_long = [(f, n) for f, n in too_long if n > limit]
            if too_long:
                names = "\n".join(f"• {Path(f).name} ({n} páginas)" for f, n in too_long[:10])
                if not messagebox.askyesno(
                    "Limite de Páginas Excedido",
                    f"O limite é {limit} páginas. Nos arquivos abaixo, as páginas após a {limit}ª serão removidas:\n\n{names}\n\nDeseja continuar?"
                ):
                    return

        self.set_running(True)
        cancel_event = self.cancel_event
        action = self.tool_action

        def worker():
            try:
                errors = action(files, str(out_path), progress_callback=self.update_progress, cancel_event=cancel_event, **kwargs)
                self.app.post(lambda: self.on_finished(errors, out_path))
            except pdf_tools.Cancelled:
                self.app.post(self.on_cancelled)
            except Exception as e:
                msg = str(e) # 'e' is unbound once the except block ends, so capture it now
                self.app.post(lambda: self.on_failed(msg))

        threading.Thread(target=worker, daemon=True).start()

    def set_running(self, running):
        if running:
            self.cancel_event = threading.Event()
            self.progressbar.set(0)
            self.lbl_status.configure(text="Iniciando...")
            self.btn_open_output.grid_remove()
            self.btn_process.configure(text="Cancelar", fg_color=DANGER, hover_color=DANGER_HOVER, command=self.cancel)
        else:
            self.cancel_event = None
            self.btn_process.configure(text=self.process_text, fg_color=self.process_colors[0],
                                       hover_color=self.process_colors[1], command=self.process, state="normal")

    def cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
            self.btn_process.configure(text="Cancelando...", state="disabled")

    def show_open_output(self, path):
        self.last_output = path
        self.btn_open_output.grid(row=0, column=1, padx=(10, 0))

    def on_finished(self, errors, out_path):
        self.set_running(False)
        self.progressbar.set(1)
        self.show_open_output(out_path)
        if errors:
            status = self.lbl_status.cget("text")
            self.lbl_status.configure(text=f"{status}  ({len(errors)} arquivo(s) com erro)")
            msg = f"{len(errors)} arquivo(s) não puderam ser processados:\n\n" + "\n".join(errors)
            messagebox.showerror("Concluído com erros", msg)

    def on_cancelled(self):
        self.set_running(False)
        self.progressbar.set(0)
        if self.tool_name in OUTPUT_FILE_TOOLS:
            self.lbl_status.configure(text="Cancelado. Nenhum arquivo foi gerado.")
        else:
            self.lbl_status.configure(text="Cancelado. Os arquivos já gerados foram mantidos.")
            self.show_open_output(self.output_dir)

    def on_failed(self, msg):
        self.set_running(False)
        self.progressbar.set(0)
        self.lbl_status.configure(text="Erro")
        messagebox.showerror("Erro", msg)

class OutputFilenameOptions(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.lbl = ctk.CTkLabel(self, text="Nome do arquivo final:")
        self.lbl.pack(side="left", padx=5)
        self.entry = ctk.CTkEntry(self, width=200, placeholder_text="Ex: arquivo_mesclado")
        self.entry.pack(side="left", padx=5)
        self.lbl_ext = ctk.CTkLabel(self, text=".pdf")
        self.lbl_ext.pack(side="left")

    def get_values(self):
        name = self.entry.get().strip()
        if not name:
            name = "arquivo_mesclado"
        if any(c in name for c in '<>:"/\\|?*'):
            raise ValueError('O nome do arquivo não pode conter os caracteres  < > : " / \\ | ? *')
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        return {"output_filename": name}

class SplitOptions(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.mode_var = ctk.StringVar(value="half")

        # Radio buttons
        self.rb_half = ctk.CTkRadioButton(self, text="50/50 (Metade)", variable=self.mode_var, value="half", command=self.on_mode_change)
        self.rb_half.grid(row=0, column=0, padx=10, pady=5, sticky="w")

        self.rb_pages = ctk.CTkRadioButton(self, text="Por Nº de Páginas", variable=self.mode_var, value="pages", command=self.on_mode_change)
        self.rb_pages.grid(row=0, column=1, padx=10, pady=5, sticky="w")

        self.rb_size = ctk.CTkRadioButton(self, text="Por Tamanho (MB)", variable=self.mode_var, value="size", command=self.on_mode_change)
        self.rb_size.grid(row=0, column=2, padx=10, pady=5, sticky="w")

        # Parameter entry
        self.param_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.param_frame.grid(row=0, column=3, padx=10, pady=0, sticky="w")

        self.lbl_param = ctk.CTkLabel(self.param_frame, text="")
        self.lbl_param.pack(side="left", padx=5)

        self.entry_param = ctk.CTkEntry(self.param_frame, width=80)
        self.entry_param.pack(side="left", padx=5)

        self.on_mode_change()

    def on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "half":
            self.param_frame.grid_remove()
        elif mode == "pages":
            self.param_frame.grid()
            self.lbl_param.configure(text="Páginas por arquivo:")
            self.entry_param.delete(0, "end")
            self.entry_param.insert(0, "10")
        elif mode == "size":
            self.param_frame.grid()
            self.lbl_param.configure(text="Tamanho (MB):")
            self.entry_param.delete(0, "end")
            self.entry_param.insert(0, "5")

    def get_values(self):
        mode = self.mode_var.get()
        val = 0
        if mode in ["pages", "size"]:
            field = "Páginas por arquivo" if mode == "pages" else "Tamanho (MB)"
            val = parse_number(self.entry_param.get(), field, 10 if mode == "pages" else 5)
            if val <= 0:
                raise ValueError(f"'{field}' deve ser maior que zero.")
        return {"split_mode": mode, "split_param": val}

    def load_values(self, values):
        if values.get("split_mode") in ("half", "pages", "size"):
            self.mode_var.set(values["split_mode"])
            self.on_mode_change()
            if values["split_mode"] != "half":
                self.entry_param.delete(0, "end")
                self.entry_param.insert(0, format_number(float(values["split_param"])))

class CompressOptions(ctk.CTkFrame):
    LEVELS = ["Muito Baixa", "Baixa", "Média", "Alta", "Muito Alta"]

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.lbl_title = ctk.CTkLabel(self, text="Nível de Compressão:")
        self.lbl_title.pack(side="left", padx=5)

        self.level_var = ctk.StringVar(value="Média")
        self.optionmenu = ctk.CTkOptionMenu(self, values=self.LEVELS, variable=self.level_var)
        self.optionmenu.pack(side="left", padx=5, pady=5)

        self.lbl_warning = ctk.CTkLabel(
            self,
            text="⚠ Quanto MAIS ALTA a compressão, MENOR a qualidade das imagens.",
            text_color="#e6aa00",
            font=ctk.CTkFont(size=12, slant="italic")
        )
        self.lbl_warning.pack(side="left", padx=10, pady=5)

    def get_values(self):
        return {"compression_level": self.level_var.get()}

    def load_values(self, values):
        if values.get("compression_level") in self.LEVELS:
            self.level_var.set(values["compression_level"])

class MarginsOptions(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.lbl_x = ctk.CTkLabel(self, text="Margem X (mm):")
        self.lbl_x.pack(side="left", padx=5)
        self.entry_x = ctk.CTkEntry(self, width=50)
        self.entry_x.insert(0, "15")
        self.entry_x.pack(side="left", padx=5)

        self.lbl_y = ctk.CTkLabel(self, text="Margem Y (mm):")
        self.lbl_y.pack(side="left", padx=5)
        self.entry_y = ctk.CTkEntry(self, width=50)
        self.entry_y.insert(0, "5")
        self.entry_y.pack(side="left", padx=5)

    def get_values(self):
        margin_x = parse_number(self.entry_x.get(), "Margem X", 15)
        margin_y = parse_number(self.entry_y.get(), "Margem Y", 5)
        # A4 is 210 x 297 mm; margins on both sides must leave room for the page content
        if margin_x * 2 >= 210 or margin_y * 2 >= 297:
            raise ValueError("As margens são grandes demais para uma página A4 (210 x 297 mm).")
        return {"margin_x_mm": margin_x, "margin_y_mm": margin_y}

    def load_values(self, values):
        for entry, key in ((self.entry_x, "margin_x_mm"), (self.entry_y, "margin_y_mm")):
            if key in values:
                entry.delete(0, "end")
                entry.insert(0, format_number(float(values[key])))

class VisualEditor(ctk.CTkFrame):
    """Single-page view with zoom and a thumbnail strip. Pages are rendered on demand,
    so memory use doesn't grow with the size of the PDF."""
    ZOOM_STEPS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
    MIN_CROP_PX = 5

    def __init__(self, master, app, tool_name, input_file, output_dir):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.tool_name = tool_name
        self.input_file = input_file
        self.output_dir = output_dir
        self.doc = None
        self.thumb_job = None
        self.resize_job = None
        self.bound_keys = []

        # Opened from memory, so the file on disk is never locked while the editor is open
        self.doc = pymupdf.open(stream=Path(input_file).read_bytes(), filetype="pdf")
        if self.doc.needs_pass:
            raise ValueError("O PDF está protegido por senha.")
        if self.doc.page_count == 0:
            raise ValueError("O PDF não possui páginas.")

        n = self.page_count = self.doc.page_count
        self.rotations = [0] * n   # extra clockwise rotation chosen by the user
        self.crops = [None] * n    # (x0, y0, x1, y1) relative to the displayed page
        self.thumb_photos = [None] * n
        self.thumb_rotations = [None] * n
        self.thumb_geometry = [None] * n
        self.thumb_cursor = 0
        self.current = 0
        self.zoom = None           # None = fit to window
        self.effective_zoom = 1.0
        self.page_photo = None
        self.image_box = None      # (x, y, width, height) of the page image on the canvas
        self.drag_start = None
        self.saving = False

        scaling = self._get_widget_scaling()
        self.base_px_per_pt = 96 / 72 * scaling # 100% zoom = physical size on a 96 dpi screen
        self.thumb_w, self.thumb_h = int(96 * scaling), int(124 * scaling)
        self.thumb_canvas_w = self.thumb_w + int(28 * scaling)
        self.cell_h = self.thumb_h + int(34 * scaling)
        self.page_margin = int(16 * scaling)

        self.setup_ui()
        self.bind_keys()
        self.draw_thumb_placeholders()
        self.show_page(0)
        self.schedule_thumbs()

    # ---------- UI ----------
    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        topbar = ctk.CTkFrame(self)
        topbar.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 6))

        self.btn_back = ctk.CTkButton(topbar, text="⬅ Voltar", width=90, command=self.go_back)
        self.btn_back.pack(side="left", padx=10, pady=10)

        ctk.CTkLabel(topbar, text=f"{self.tool_name} — {Path(self.input_file).name}", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=10)

        self.btn_save = ctk.CTkButton(topbar, text="Salvar e Exportar", fg_color="#2ecc71", hover_color="#27ae60", command=self.save_and_export)
        self.btn_save.pack(side="right", padx=10, pady=10)

        hint = "Arraste sobre a página para marcar o corte" if self.tool_name == "Cortar PDF" else ""
        self.lbl_status = ctk.CTkLabel(topbar, text=hint, text_color="gray")
        self.lbl_status.pack(side="right", padx=10)

        # Toolbar: navigation, zoom and tool actions
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=20, pady=4)
        small = dict(width=34, height=30)

        def separator():
            ctk.CTkFrame(toolbar, width=2, height=24, fg_color=("gray70", "gray35")).pack(side="left", padx=10)

        self.btn_prev = ctk.CTkButton(toolbar, text="◀", command=lambda: self.show_page(self.current - 1), **small)
        self.btn_prev.pack(side="left", padx=(0, 8))
        self.entry_page = ctk.CTkEntry(toolbar, width=50, height=30, justify="center")
        self.entry_page.pack(side="left")
        self.entry_page.bind("<Return>", self.on_page_entry)
        ctk.CTkLabel(toolbar, text=f"de {self.page_count}").pack(side="left", padx=(4, 8))
        self.btn_next = ctk.CTkButton(toolbar, text="▶", command=lambda: self.show_page(self.current + 1), **small)
        self.btn_next.pack(side="left")

        separator()
        ctk.CTkButton(toolbar, text="−", command=lambda: self.step_zoom(-1), **small).pack(side="left")
        self.lbl_zoom = ctk.CTkLabel(toolbar, text="100%", width=52)
        self.lbl_zoom.pack(side="left")
        ctk.CTkButton(toolbar, text="+", command=lambda: self.step_zoom(1), **small).pack(side="left")
        self.btn_fit = ctk.CTkButton(toolbar, text="Ajustar", width=70, height=30, command=self.zoom_fit)
        self.btn_fit.pack(side="left", padx=(6, 0))

        separator()
        if self.tool_name == "Girar PDF":
            # The arrow glyphs alone look almost identical at this size, so the buttons also say the direction
            for label, all_pages in (("Página:", False), ("Todas:", True)):
                ctk.CTkLabel(toolbar, text=label).pack(side="left", padx=(0, 4))
                ctk.CTkButton(toolbar, text="↺ Esq.", width=64, height=30, command=lambda a=all_pages: self.rotate(-90, all_pages=a)).pack(side="left", padx=(0, 4))
                ctk.CTkButton(toolbar, text="Dir. ↻", width=64, height=30, command=lambda a=all_pages: self.rotate(90, all_pages=a)).pack(side="left", padx=(0, 12))
        elif self.tool_name == "Cortar PDF":
            ctk.CTkButton(toolbar, text="Aplicar a Todas", width=120, height=30, command=self.apply_crop_to_all).pack(side="left", padx=(0, 4))
            ctk.CTkButton(toolbar, text="Limpar Corte", width=100, height=30, command=self.clear_crop).pack(side="left")

        # Body: thumbnails + page
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(6, 20))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(2, weight=1)

        bg = self.canvas_bg()
        self.thumbs = tk.Canvas(body, width=self.thumb_canvas_w, bg=bg, highlightthickness=0,
                                yscrollincrement=max(self.cell_h // 4, 1))
        self.thumbs.grid(row=0, column=0, sticky="ns")
        thumbs_scroll = ctk.CTkScrollbar(body, command=self.thumbs.yview)
        thumbs_scroll.grid(row=0, column=1, sticky="ns")
        self.thumbs.configure(yscrollcommand=thumbs_scroll.set, scrollregion=(0, 0, self.thumb_canvas_w, self.page_count * self.cell_h))
        self.thumbs.bind("<Button-1>", self.on_thumb_click)
        self.thumbs.bind("<MouseWheel>", lambda e: self.thumbs.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.thumbs.bind("<Configure>", lambda e: self.schedule_thumbs())

        page_frame = ctk.CTkFrame(body, fg_color="transparent")
        page_frame.grid(row=0, column=2, sticky="nsew", padx=(10, 0))
        page_frame.grid_rowconfigure(0, weight=1)
        page_frame.grid_columnconfigure(0, weight=1)

        self.page_canvas = tk.Canvas(page_frame, bg=bg, highlightthickness=0, xscrollincrement=40, yscrollincrement=40)
        self.page_canvas.grid(row=0, column=0, sticky="nsew")
        page_vscroll = ctk.CTkScrollbar(page_frame, command=self.page_canvas.yview)
        page_vscroll.grid(row=0, column=1, sticky="ns")
        page_hscroll = ctk.CTkScrollbar(page_frame, orientation="horizontal", command=self.page_canvas.xview)
        page_hscroll.grid(row=1, column=0, sticky="ew")
        self.page_canvas.configure(xscrollcommand=page_hscroll.set, yscrollcommand=page_vscroll.set)

        self.page_canvas.bind("<Configure>", self.on_page_canvas_resize)
        self.page_canvas.bind("<MouseWheel>", self.on_page_wheel)
        # Middle button pans in both tools; in the rotate tool the left button pans too
        self.page_canvas.bind("<ButtonPress-2>", lambda e: self.page_canvas.scan_mark(e.x, e.y))
        self.page_canvas.bind("<B2-Motion>", lambda e: self.page_canvas.scan_dragto(e.x, e.y, gain=1))
        if self.tool_name == "Cortar PDF":
            self.page_canvas.configure(cursor="crosshair")
            self.page_canvas.bind("<ButtonPress-1>", self.on_crop_press)
            self.page_canvas.bind("<B1-Motion>", self.on_crop_drag)
            self.page_canvas.bind("<ButtonRelease-1>", self.on_crop_release)
        else:
            self.page_canvas.configure(cursor="fleur")
            self.page_canvas.bind("<ButtonPress-1>", lambda e: self.page_canvas.scan_mark(e.x, e.y))
            self.page_canvas.bind("<B1-Motion>", lambda e: self.page_canvas.scan_dragto(e.x, e.y, gain=1))

    def canvas_bg(self):
        return "#1E1E1E" if ctk.get_appearance_mode() == "Dark" else "#D9D9D9"

    def on_theme_change(self):
        for canvas in (self.thumbs, self.page_canvas):
            canvas.configure(bg=self.canvas_bg())
        self.thumbs.itemconfigure("label", fill=self.text_color())
        self.thumbs.itemconfigure("placeholder", fill=self.placeholder_color())

    def shadow_color(self):
        return "#0A0A0A" if ctk.get_appearance_mode() == "Dark" else "#A8A8A8"

    def text_color(self):
        return "#DCE4EE" if ctk.get_appearance_mode() == "Dark" else "#1A1A1A"

    def placeholder_color(self):
        return "#3A3A3A" if ctk.get_appearance_mode() == "Dark" else "#BDBDBD"

    def bind_keys(self):
        bindings = {
            "<Left>": lambda: self.show_page(self.current - 1), "<Prior>": lambda: self.show_page(self.current - 1),
            "<Right>": lambda: self.show_page(self.current + 1), "<Next>": lambda: self.show_page(self.current + 1),
            "<Home>": lambda: self.show_page(0), "<End>": lambda: self.show_page(self.page_count - 1),
            "<Control-plus>": lambda: self.step_zoom(1), "<Control-equal>": lambda: self.step_zoom(1),
            "<Control-minus>": lambda: self.step_zoom(-1), "<Control-0>": self.zoom_fit,
        }
        for sequence, action in bindings.items():
            def handler(event, action=action):
                if isinstance(event.widget, tk.Entry): # keep arrow keys working inside the page number field
                    return
                action()
            self.app.bind(sequence, handler)
            self.bound_keys.append(sequence)

    def destroy(self):
        for sequence in self.bound_keys:
            self.app.unbind(sequence)
        for job in (self.thumb_job, self.resize_job):
            if job:
                self.after_cancel(job)
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()
        super().destroy()

    # ---------- Navigation ----------
    def on_page_entry(self, event):
        try:
            self.show_page(int(self.entry_page.get()) - 1)
        except ValueError:
            pass
        self.update_page_entry()
        self.page_canvas.focus_set()

    def update_page_entry(self):
        self.entry_page.delete(0, "end")
        self.entry_page.insert(0, str(self.current + 1))

    def show_page(self, index):
        if not 0 <= index < self.page_count:
            return
        changed = index != self.current
        self.current = index
        self.update_page_entry()
        self.btn_prev.configure(state="normal" if index > 0 else "disabled")
        self.btn_next.configure(state="normal" if index < self.page_count - 1 else "disabled")
        self.highlight_thumb()
        self.render_page(keep_scroll=not changed)

    def on_thumb_click(self, event):
        self.show_page(int(self.thumbs.canvasy(event.y) // self.cell_h))

    # ---------- Thumbnails ----------
    def draw_thumb_placeholders(self):
        fill, text = self.placeholder_color(), self.text_color()
        for i in range(self.page_count):
            y = i * self.cell_h + 8
            x = (self.thumb_canvas_w - self.thumb_w) / 2
            self.thumbs.create_rectangle(x, y, x + self.thumb_w, y + self.thumb_h, fill=fill, outline="", tags=("placeholder", f"placeholder{i}"))
            self.thumbs.create_text(self.thumb_canvas_w / 2, y + self.thumb_h + 13, text=str(i + 1), fill=text, tags=("label",))

    def highlight_thumb(self):
        self.thumbs.delete("selection")
        y = self.current * self.cell_h
        self.thumbs.create_rectangle(3, y + 3, self.thumb_canvas_w - 3, y + self.cell_h - 3, outline="#3B8ED0", width=3, tags=("selection",))
        # Keep the current thumbnail visible
        top = self.thumbs.canvasy(0)
        height = self.thumbs.winfo_height()
        if height < self.cell_h:
            return # not laid out yet: centering math would scroll the strip past the first page
        if y < top or y + self.cell_h > top + height:
            total = self.page_count * self.cell_h
            self.thumbs.yview_moveto(max(y - (height - self.cell_h) / 2, 0) / total)
            self.schedule_thumbs()

    def render_thumb(self, i):
        page = self.doc[i]
        rotation = self.rotations[i]
        w, h = page.rect.width, page.rect.height
        if rotation % 180:
            w, h = h, w
        scale = min(self.thumb_w / w, self.thumb_h / h)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale).prerotate(rotation))
        self.thumb_photos[i] = tk.PhotoImage(master=self, data=pix.tobytes("ppm"))
        self.thumb_rotations[i] = rotation

        x = (self.thumb_canvas_w - pix.width) / 2
        y = i * self.cell_h + 8 + (self.thumb_h - pix.height) / 2
        self.thumbs.delete(f"placeholder{i}", f"thumb{i}")
        self.thumbs.create_image(x, y, anchor="nw", image=self.thumb_photos[i], tags=(f"thumb{i}",))
        self.thumb_geometry[i] = (x, y, pix.width, pix.height)
        self.draw_thumb_crop(i)
        self.thumbs.tag_raise("selection")

    def thumb_is_stale(self, i):
        return self.thumb_photos[i] is None or self.thumb_rotations[i] != self.rotations[i]

    def schedule_thumbs(self):
        if self.thumb_job is None:
            self.thumb_job = self.after(1, self.render_next_thumb)

    def render_next_thumb(self):
        """Renders one thumbnail per event loop turn, visible ones first."""
        self.thumb_job = None
        first_visible = max(int(self.thumbs.canvasy(0) // self.cell_h), 0)
        last_visible = min(first_visible + self.thumbs.winfo_height() // self.cell_h + 1, self.page_count - 1)
        target = next((i for i in range(first_visible, last_visible + 1) if self.thumb_is_stale(i)), None)
        if target is None:
            while self.thumb_cursor < self.page_count and not self.thumb_is_stale(self.thumb_cursor):
                self.thumb_cursor += 1
            if self.thumb_cursor >= self.page_count:
                return
            target = self.thumb_cursor
        self.render_thumb(target)
        self.thumb_job = self.after(1, self.render_next_thumb)

    def draw_thumb_crop(self, i):
        self.thumbs.delete(f"thumbcrop{i}")
        crop, geometry = self.crops[i], self.thumb_geometry[i]
        if crop and geometry:
            x, y, w, h = geometry
            self.thumbs.create_rectangle(x + crop[0] * w, y + crop[1] * h, x + crop[2] * w, y + crop[3] * h,
                                         outline="#E5534B", width=2, tags=(f"thumbcrop{i}",))

    # ---------- Page rendering and zoom ----------
    def render_page(self, keep_scroll=True):
        canvas = self.page_canvas
        page = self.doc[self.current]
        rotation = self.rotations[self.current]
        w_pt, h_pt = page.rect.width, page.rect.height
        if rotation % 180:
            w_pt, h_pt = h_pt, w_pt

        cw, ch = max(canvas.winfo_width(), 100), max(canvas.winfo_height(), 100)
        if self.zoom is None:
            px_per_pt = min((cw - 2 * self.page_margin) / w_pt, (ch - 2 * self.page_margin) / h_pt)
        else:
            px_per_pt = self.zoom * self.base_px_per_pt
        px_per_pt = max(min(px_per_pt, 8000 / max(w_pt, h_pt)), 0.02) # cap the bitmap size
        self.effective_zoom = px_per_pt / self.base_px_per_pt
        self.lbl_zoom.configure(text=f"{round(self.effective_zoom * 100)}%")

        x_view, y_view = canvas.xview()[0], canvas.yview()[0]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(px_per_pt, px_per_pt).prerotate(rotation))
        self.page_pix = pix
        self.page_photo = tk.PhotoImage(master=self, data=pix.tobytes("ppm"))
        self.page_photo_dim = None # built on demand for the crop overlay

        region_w = max(cw, pix.width + 2 * self.page_margin)
        region_h = max(ch, pix.height + 2 * self.page_margin)
        x0, y0 = (region_w - pix.width) // 2, (region_h - pix.height) // 2
        canvas.delete("all")
        canvas.create_rectangle(x0 + 3, y0 + 3, x0 + pix.width + 3, y0 + pix.height + 3, fill=self.shadow_color(), outline="")
        canvas.create_image(x0, y0, anchor="nw", image=self.page_photo, tags=("page",))
        canvas.configure(scrollregion=(0, 0, region_w, region_h))
        self.image_box = (x0, y0, pix.width, pix.height)
        canvas.xview_moveto(x_view if keep_scroll else 0)
        canvas.yview_moveto(y_view if keep_scroll else 0)
        self.draw_page_crop()

    def on_page_canvas_resize(self, event):
        if self.resize_job:
            self.after_cancel(self.resize_job)
        self.resize_job = self.after(80, self.on_resize_done)

    def on_resize_done(self):
        self.resize_job = None
        self.render_page()

    def step_zoom(self, direction):
        current = self.effective_zoom
        if direction > 0:
            candidates = [z for z in self.ZOOM_STEPS if z > current + 0.01]
            self.zoom = candidates[0] if candidates else self.ZOOM_STEPS[-1]
        else:
            candidates = [z for z in self.ZOOM_STEPS if z < current - 0.01]
            self.zoom = candidates[-1] if candidates else self.ZOOM_STEPS[0]
        self.render_page()

    def zoom_fit(self):
        self.zoom = None
        self.render_page(keep_scroll=False)

    def on_page_wheel(self, event):
        step = -1 if event.delta > 0 else 1
        if event.state & 0x4: # Ctrl + wheel zooms
            self.step_zoom(-step)
            return
        if event.state & 0x1: # Shift + wheel scrolls sideways
            self.page_canvas.xview_scroll(step, "units")
            return
        top, bottom = self.page_canvas.yview()
        if top <= 0 and bottom >= 1:
            self.show_page(self.current + step) # whole page visible: the wheel flips pages
        else:
            self.page_canvas.yview_scroll(step, "units")

    # ---------- Rotate ----------
    def rotate(self, delta, all_pages=False):
        pages = range(self.page_count) if all_pages else [self.current]
        for i in pages:
            self.rotations[i] = (self.rotations[i] + delta) % 360
        if all_pages:
            self.thumb_cursor = 0
            self.schedule_thumbs()
        else:
            self.render_thumb(self.current)
        self.render_page(keep_scroll=False)

    # ---------- Crop ----------
    def clamp_to_image(self, event):
        x0, y0, w, h = self.image_box
        x = min(max(self.page_canvas.canvasx(event.x), x0), x0 + w)
        y = min(max(self.page_canvas.canvasy(event.y), y0), y0 + h)
        return x, y

    def relative_rect(self, p1, p2):
        x0, y0, w, h = self.image_box
        return ((min(p1[0], p2[0]) - x0) / w, (min(p1[1], p2[1]) - y0) / h,
                (max(p1[0], p2[0]) - x0) / w, (max(p1[1], p2[1]) - y0) / h)

    def on_crop_press(self, event):
        self.page_canvas.focus_set()
        self.drag_start = self.clamp_to_image(event)

    def on_crop_drag(self, event):
        if self.drag_start:
            self.draw_page_crop(self.relative_rect(self.drag_start, self.clamp_to_image(event)))

    def on_crop_release(self, event):
        if not self.drag_start:
            return
        end = self.clamp_to_image(event)
        if abs(end[0] - self.drag_start[0]) < self.MIN_CROP_PX or abs(end[1] - self.drag_start[1]) < self.MIN_CROP_PX:
            crop = None # a plain click (or tiny drag) clears the selection instead of creating an empty crop box
        else:
            crop = self.relative_rect(self.drag_start, end)
        self.drag_start = None
        self.set_crop(self.current, crop)
        self.draw_page_crop()

    def set_crop(self, index, crop):
        self.crops[index] = crop
        self.draw_thumb_crop(index)
        marked = sum(1 for c in self.crops if c)
        self.lbl_status.configure(text=f"Corte marcado em {marked} de {self.page_count} página(s)" if marked else "Arraste sobre a página para marcar o corte")

    def dimmed_page_photo(self):
        if self.page_photo_dim is None:
            pix = self.page_pix
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).point(lambda v: v * 45 // 100)
            buf = io.BytesIO()
            img.save(buf, format="PPM")
            self.page_photo_dim = tk.PhotoImage(master=self, data=buf.getvalue())
        return self.page_photo_dim

    def draw_page_crop(self, crop=None):
        canvas = self.page_canvas
        canvas.delete("crop")
        crop = crop or self.crops[self.current]
        if not self.image_box:
            return
        if not crop:
            canvas.itemconfigure("page", image=self.page_photo)
            return
        x, y, w, h = self.image_box
        px0, py0 = int(crop[0] * w), int(crop[1] * h)
        px1, py1 = max(int(crop[2] * w), px0 + 1), max(int(crop[3] * h), py0 + 1)
        # The whole page is shown dimmed and the selected area is pasted on top at full brightness
        # (Tk stipple patterns look like a coarse checkerboard on Windows)
        canvas.itemconfigure("page", image=self.dimmed_page_photo())
        if not hasattr(self, "crop_photo"):
            self.crop_photo = tk.PhotoImage(master=self)
        self.crop_photo.blank()
        self.tk.call(self.crop_photo.name, "copy", self.page_photo.name, "-from", px0, py0, px1, py1, "-shrink")
        canvas.create_image(x + px0, y + py0, anchor="nw", image=self.crop_photo, tags=("crop",))
        canvas.create_rectangle(x + px0, y + py0, x + px1, y + py1, outline="#E5534B", width=2, dash=(6, 4), tags=("crop",))

    def apply_crop_to_all(self):
        crop = self.crops[self.current] or next((c for c in self.crops if c), None)
        if not crop:
            messagebox.showinfo("Aviso", "Desenhe o corte em uma página primeiro.")
            return
        # Crops are relative, so pages of different sizes get the same proportional area
        for i in range(self.page_count):
            self.set_crop(i, crop)
        self.draw_page_crop()

    def clear_crop(self):
        self.set_crop(self.current, None)
        self.draw_page_crop()

    # ---------- Save / leave ----------
    def has_changes(self):
        return any(self.rotations) or any(self.crops)

    def confirm_leave(self):
        if self.saving:
            messagebox.showinfo("Aguarde", "O arquivo ainda está sendo salvo.")
            return False
        if self.has_changes():
            return messagebox.askyesno("Descartar alterações?", "As alterações feitas neste editor ainda não foram salvas.\n\nDeseja sair mesmo assim?")
        return True

    def save_and_export(self):
        if self.saving:
            return
        if self.tool_name == "Girar PDF":
            action = pdf_tools.rotate_pdf_visual
            arg = {i: r for i, r in enumerate(self.rotations) if r}
            if not arg:
                messagebox.showwarning("Aviso", "Nenhuma página foi girada.")
                return
        else:
            action = pdf_tools.crop_pdf_visual
            arg = {i: c for i, c in enumerate(self.crops) if c}
            if not arg:
                messagebox.showwarning("Aviso", "Nenhum corte desenhado.")
                return

        self.saving = True
        self.btn_save.configure(state="disabled", text="Salvando...")
        self.lbl_status.configure(text="Salvando...")
        input_file, output_dir = self.input_file, self.output_dir

        def worker():
            try:
                out_file = action(input_file, output_dir, arg)
                self.app.post(lambda: self.on_saved(out_file))
            except Exception as e:
                msg = str(e)
                self.app.post(lambda: self.on_save_failed(msg))
        threading.Thread(target=worker, daemon=True).start()

    def on_saved(self, out_file):
        self.saving = False
        self.app.open_tool(self.tool_name, preset_files=[self.input_file], force=True)
        if messagebox.askyesno("Concluído", f"Arquivo salvo como '{Path(out_file).name}'.\n\nDeseja abrir a pasta de destino?"):
            open_in_explorer(out_file)

    def on_save_failed(self, msg):
        self.saving = False
        self.btn_save.configure(state="normal", text="Salvar e Exportar")
        self.lbl_status.configure(text="Erro ao salvar")
        messagebox.showerror("Erro", f"Ocorreu um erro: {msg}")

    def go_back(self):
        self.app.open_tool(self.tool_name, preset_files=[self.input_file])

class Dashboard(ctk.CTkFrame):
    def __init__(self, master, open_tool_callback, tools):
        super().__init__(master, fg_color="transparent")
        self.open_tool_callback = open_tool_callback

        lbl_title = ctk.CTkLabel(self, text="Selecione uma Ferramenta", font=ctk.CTkFont(size=28, weight="bold"))
        lbl_title.pack(pady=30)

        grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        grid_frame.pack(expand=True, fill="both", padx=20, pady=20)
        grid_frame.grid_columnconfigure((0, 1, 2), weight=1)

        row = 0
        col = 0
        for name, (desc, action, extra) in tools.items():
            card = ctk.CTkFrame(grid_frame, corner_radius=10)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")

            font_size = 14 if len(name) > 14 else 15
            btn = ctk.CTkButton(card, text=name, font=ctk.CTkFont(size=font_size, weight="bold"),
                                command=lambda n=name: self.open_tool_callback(n),
                                height=60, fg_color="transparent", text_color=("black", "white"), hover_color=("gray85", "gray25"))
            btn.pack(fill="x", pady=(10, 0), padx=10)

            lbl = ctk.CTkLabel(card, text=desc, font=ctk.CTkFont(size=12), text_color="gray", wraplength=180)
            lbl.pack(pady=(0, 10), padx=10)

            col += 1
            if col > 2:
                col = 0
                row += 1

class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        # Loads the tkdnd Tcl extension into this interpreter (CTk can't inherit from TkinterDnD.Tk)
        self.TkdndVersion = TkinterDnD._require(self)

        self.title("PDF Tools")
        self.geometry("1100x720")
        self.minsize(1000, 650)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Worker threads never touch Tk directly: they post callables that run on the main thread
        self.ui_queue = queue.Queue()
        self.poll_ui_queue()

        # Set Application Icon
        if getattr(sys, 'frozen', False):
            base_path = Path(sys._MEIPASS)
        else:
            base_path = Path(__file__).parent

        icon_path = base_path / "PDF.ico"
        if icon_path.exists():
            self.iconbitmap(str(icon_path))

        self.tools = {
            "Juntar PDFs": ("Junte vários PDFs em um só", pdf_tools.merge_pdfs, OutputFilenameOptions),
            "Dividir PDF": ("Divida um PDF em partes", pdf_tools.split_pdfs, SplitOptions),
            "Comprimir": ("Reduza o tamanho do PDF", pdf_tools.compress_pdfs, CompressOptions),
            "Imagens para PDF": ("Converta imagens em PDF", pdf_tools.images_to_pdf, OutputFilenameOptions),
            "PDF para Imagens": ("Extraia páginas como imagens", pdf_tools.pdf_to_images, None),
            "Girar PDF": ("Gire as páginas visualmente", None, None),
            "Cortar PDF": ("Corte as áreas visualmente", None, None),
            "Adicionar Margens": ("Adicione bordas brancas", pdf_tools.add_margins, MarginsOptions),
        }

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(len(self.tools) + 3, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="PDF Tools", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.nav_buttons = {}
        self.nav_buttons["Início"] = self.make_nav_button("🏠  Início", self.show_home)
        self.nav_buttons["Início"].grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        ctk.CTkLabel(self.sidebar_frame, text="FERRAMENTAS", text_color="gray", font=ctk.CTkFont(size=11, weight="bold")).grid(row=2, column=0, padx=20, sticky="w")
        for index, name in enumerate(self.tools):
            button = self.make_nav_button(name, lambda n=name: self.open_tool(n))
            button.grid(row=index + 3, column=0, padx=10, pady=1, sticky="ew")
            self.nav_buttons[name] = button

        self.theme_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.theme_frame.grid(row=len(self.tools) + 4, column=0, padx=10, pady=20, sticky="s")

        self.lbl_sun = ctk.CTkLabel(self.theme_frame, text="☀", font=ctk.CTkFont(size=24))
        self.lbl_sun.pack(side="left", padx=5)

        self.theme_switch = ctk.CTkSwitch(self.theme_frame, text="", width=40, command=self.toggle_theme)

        theme = settings.get("theme") or ("Dark" if darkdetect.theme() == "Dark" else "Light")
        ctk.set_appearance_mode(theme)
        if theme == "Dark":
            self.theme_switch.select()
        else:
            self.theme_switch.deselect()
        apply_treeview_style(self)

        self.theme_switch.pack(side="left", padx=0)

        self.lbl_moon = ctk.CTkLabel(self.theme_frame, text="☾", font=ctk.CTkFont(size=24))
        self.lbl_moon.pack(side="left", padx=5)

        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew")

        self.current_view = None
        self.show_home()

    def make_nav_button(self, text, command):
        return ctk.CTkButton(self.sidebar_frame, text=text, command=command, anchor="w", height=32,
                             fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray75", "gray30"))

    def set_active_nav(self, name):
        for key, button in self.nav_buttons.items():
            active = key == name
            button.configure(fg_color=("#3B8ED0", "#1F6AA5") if active else "transparent",
                             text_color=("white", "white") if active else ("gray10", "gray90"))

    def post(self, func):
        """Thread-safe: schedules func to run on the Tk main thread."""
        self.ui_queue.put(func)

    def poll_ui_queue(self):
        while True:
            try:
                func = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                func()
            except tk.TclError:
                pass # the widget it targeted was closed in the meantime
            except Exception:
                self.report_callback_exception(*sys.exc_info())
        self.after(50, self.poll_ui_queue)

    def toggle_theme(self):
        mode = "Dark" if self.theme_switch.get() == 1 else "Light"
        ctk.set_appearance_mode(mode)
        settings.set("theme", mode)
        apply_treeview_style(self)
        if hasattr(self.current_view, "on_theme_change"):
            self.current_view.on_theme_change()

    def can_leave_current_view(self):
        return not hasattr(self.current_view, "confirm_leave") or self.current_view.confirm_leave()

    def on_close(self):
        if self.can_leave_current_view():
            self.destroy()

    def clear_main_frame(self):
        for child in self.main_frame.winfo_children():
            child.destroy()
        self.current_view = None

    def show_home(self, force=False):
        if not force and not self.can_leave_current_view():
            return
        self.clear_main_frame()
        self.current_view = Dashboard(self.main_frame, self.open_tool, self.tools)
        self.current_view.pack(fill="both", expand=True)
        self.set_active_nav("Início")

    def open_tool(self, name, preset_files=None, force=False):
        view = self.current_view
        if not force and not preset_files and isinstance(view, ToolView) and view.tool_name == name:
            return # already open: keep the current file list
        if not force and not self.can_leave_current_view():
            return
        desc, action, options = self.tools[name]
        self.clear_main_frame()
        self.current_view = ToolView(self.main_frame, self, name, action, options, preset_files=preset_files)
        self.current_view.pack(fill="both", expand=True)
        self.set_active_nav(name)

    def open_visual_editor(self, tool_name, input_file, output_dir):
        self.clear_main_frame()
        try:
            self.current_view = VisualEditor(self.main_frame, self, tool_name, input_file, output_dir)
        except Exception as e:
            # e.g. corrupted or password-protected PDF; without this the window was left blank
            messagebox.showerror("Erro", f"Não foi possível abrir '{Path(input_file).name}':\n\n{e}")
            self.open_tool(tool_name, preset_files=[input_file], force=True)
            return
        self.current_view.pack(fill="both", expand=True)
        self.set_active_nav(tool_name)

if __name__ == "__main__":
    app = App()
    app.mainloop()
