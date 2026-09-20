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

import pdf_tools
import settings
from pdf_tools import format_size

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp"]
SINGLE_FILE_TOOLS = ["Girar PDF", "Cortar PDF"]
ALLOW_PROTECTED_TOOLS = ["Remover Senha"]
OUTPUT_FILE_TOOLS = ["Juntar PDFs", "Imagens para PDF"]
DROP_HIGHLIGHT = ("#3B8ED0", "#1F6AA5")
LIST_BG = ("#EBEBEB", "#2B2B2B")
DANGER = ("#D9534F", "#C9302C")
DANGER_HOVER = ("#C9302C", "#A52A2A")

def parse_number(text, field_name, default):
    """Converte texto para número aceitando ponto ou vírgula como separador decimal."""
    text = text.strip().replace(",", ".")
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"Valor inválido em '{field_name}': {text}")
    if not math.isfinite(value):
        raise ValueError(f"Valor inválido em '{field_name}': {text}")
    if value < 0:
        raise ValueError(f"'{field_name}' não pode ser negativo.")
    return value

def format_number(value):
    return f"{value:g}".replace(".", ",")

def shorten_path(path, max_chars=60):
    """Encurta o caminho do arquivo para exibição legível."""
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
    """Chave de ordenação natural para strings com números."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]

def open_in_explorer(path):
    """Abre a pasta ou o arquivo no gerenciador de arquivos do sistema."""
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
    """Aplica estilo visual ao Treeview conforme o tema ativo."""
    dark = ctk.get_appearance_mode() == "Dark"
    bg = LIST_BG[1] if dark else LIST_BG[0]
    fg, head_bg, selected = ("#DCE4EE", "#333333", "#1F6AA5") if dark else ("#1A1A1A", "#D6D6D6", "#3B8ED0")
    if not hasattr(root, "_tree_fonts"):
        root._tree_fonts = (tkfont.Font(root=root, family="Segoe UI", size=10),
                            tkfont.Font(root=root, family="Segoe UI", size=10, weight="bold"))
    font, head_font = root._tree_fonts
    style = ttk.Style(root)
    style.theme_use("default")
    style.configure("Files.Treeview", background=bg, fieldbackground=bg, foreground=fg, font=font,
                    rowheight=font.metrics("linespace") + 10, borderwidth=0)
    style.map("Files.Treeview", background=[("selected", selected)], foreground=[("selected", "#FFFFFF")])
    style.configure("Files.Treeview.Heading", background=head_bg, foreground=fg, font=head_font, relief="flat", padding=(6, 4))
    style.map("Files.Treeview.Heading", background=[("active", head_bg)])
    style.layout("Files.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

class FileList(ctk.CTkFrame):
    """Tabela de arquivos selecionados com ordenação e gerenciamento."""

    def __init__(self, master, app, is_image_list, single_file, on_change):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.is_image_list = is_image_list
        self.single_file = single_file
        self.on_change = on_change
        self.info = {}
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
        """Lê informações dos arquivos em segundo plano."""
        if not paths:
            return
        def work():
            for p in paths:
                info = self.read_info(p)
                self.app.post(lambda p=p, info=info: self.set_info(p, info))
        threading.Thread(target=work, daemon=True).start()

    def read_info(self, path):
        info = {"label": "inválido", "size": 0, "valid": False, "pages": 0, "protected": False}
        try:
            info["size"] = os.path.getsize(path)
            if self.is_image_list:
                with Image.open(path) as im:
                    w, h = im.size
                    if im.getexif().get(0x0112) in (5, 6, 7, 8):  # inverte largura e altura conforme orientação EXIF
                        w, h = h, w
                info.update(label=f"{w} × {h}", valid=True)
            else:
                with pymupdf.open(path) as doc:
                    if doc.needs_pass:
                        info.update(label="🔒 senha", protected=True)
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

    def invalid_files(self, allow_protected=False):
        """Retorna os arquivos inválidos para processamento."""
        return [f for f in self.files if f in self.info and not self.info[f]["valid"]
                and not (allow_protected and self.info[f].get("protected"))]

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

        self.lbl_title = ctk.CTkLabel(self, text=self.tool_name, font=ctk.CTkFont(size=24, weight="bold"))
        self.lbl_title.grid(row=0, column=0, pady=(20, 10), padx=20, sticky="w")

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

        for widget in (self, self.file_list.tree):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self.on_drop_enter)
            widget.dnd_bind("<<DropLeave>>", self.on_drop_leave)
            widget.dnd_bind("<<Drop>>", self.on_drop)

        self.out_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.out_frame.grid(row=3, column=0, padx=20, pady=(0, 5), sticky="ew")

        self.btn_select_out = ctk.CTkButton(self.out_frame, text="Pasta de Destino", command=self.select_out_dir)
        self.btn_select_out.pack(side="left")

        self.btn_reset_out = ctk.CTkButton(self.out_frame, text="↺ Usar pasta do original", width=160, fg_color="transparent",
                                           text_color=("gray10", "gray90"), hover_color=("gray80", "gray30"), command=self.reset_out_dir)

        self.lbl_out_dir = ctk.CTkLabel(self.out_frame, text="", anchor="w")
        self.lbl_out_dir.pack(side="left", padx=10, fill="x", expand=True)

        self.extra_options_frame = None
        if extra_options_widget:
            self.extra_options_frame = extra_options_widget(self)
            self.extra_options_frame.grid(row=4, column=0, padx=20, pady=5, sticky="ew")
            saved_options = settings.get(self.options_key)
            if saved_options and hasattr(self.extra_options_frame, "load_values"):
                try:
                    self.extra_options_frame.load_values(saved_options)
                except Exception:
                    pass

        self.process_text = "Abrir Editor Visual" if self.single_file else "Processar"
        self.btn_process = ctk.CTkButton(self, text=self.process_text, font=ctk.CTkFont(size=16, weight="bold"), height=40, width=180, command=self.process)
        self.btn_process.grid(row=5, column=0, pady=10)
        self.process_colors = (self.btn_process.cget("fg_color"), self.btn_process.cget("hover_color"))

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
        """Adiciona arquivos e pastas compatíveis à lista."""
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

        invalid = self.file_list.invalid_files(allow_protected=self.tool_name in ALLOW_PROTECTED_TOOLS)
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
                msg = str(e)
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

        self.rb_half = ctk.CTkRadioButton(self, text="50/50 (Metade)", variable=self.mode_var, value="half", command=self.on_mode_change)
        self.rb_half.grid(row=0, column=0, padx=10, pady=5, sticky="w")

        self.rb_pages = ctk.CTkRadioButton(self, text="Por Nº de Páginas", variable=self.mode_var, value="pages", command=self.on_mode_change)
        self.rb_pages.grid(row=0, column=1, padx=10, pady=5, sticky="w")

        self.rb_size = ctk.CTkRadioButton(self, text="Por Tamanho (MB)", variable=self.mode_var, value="size", command=self.on_mode_change)
        self.rb_size.grid(row=0, column=2, padx=10, pady=5, sticky="w")

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
    DPI_CHOICES = {"Manter original": 0, "300 dpi (gráfica)": 300,
                   "150 dpi (impressora comum)": 150, "96 dpi (leitura na tela)": 96}

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="w")

        self.lbl_title = ctk.CTkLabel(row1, text="Nível de Compressão:")
        self.lbl_title.pack(side="left", padx=5)

        self.level_var = ctk.StringVar(value="Média")
        self.optionmenu = ctk.CTkOptionMenu(row1, values=self.LEVELS, variable=self.level_var)
        self.optionmenu.pack(side="left", padx=5, pady=5)

        ctk.CTkLabel(row1, text="Resolução das imagens:").pack(side="left", padx=(15, 5))
        self.dpi_menu = ctk.CTkOptionMenu(row1, width=210, values=list(self.DPI_CHOICES))
        self.dpi_menu.set("Manter original")
        self.dpi_menu.pack(side="left", padx=5, pady=5)

        self.lbl_warning = ctk.CTkLabel(
            self,
            text="⚠ Quanto MAIS ALTA a compressão, MENOR a qualidade das imagens. "
                 "Limitar a resolução costuma reduzir mais o arquivo do que baixar a qualidade.",
            text_color="#e6aa00",
            font=ctk.CTkFont(size=12, slant="italic"),
            wraplength=820, justify="left"
        )
        self.lbl_warning.grid(row=1, column=0, sticky="w", padx=5, pady=(4, 0))

    def get_values(self):
        return {"compression_level": self.level_var.get(),
                "max_dpi": self.DPI_CHOICES[self.dpi_menu.get()]}

    def load_values(self, values):
        if values.get("compression_level") in self.LEVELS:
            self.level_var.set(values["compression_level"])
        for label, dpi in self.DPI_CHOICES.items():
            if dpi == values.get("max_dpi"):
                self.dpi_menu.set(label)

class MarginsOptions(ctk.CTkFrame):
    FIELDS = (("Esquerda", "margin_left_mm", 15), ("Direita", "margin_right_mm", 15),
              ("Superior", "margin_top_mm", 5), ("Inferior", "margin_bottom_mm", 5))
    A4_W_MM, A4_H_MM = 210, 297

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row1, text="Margens (mm):").pack(side="left", padx=(5, 10))

        self.entries = {}
        self.labels = {}
        for label, key, default in self.FIELDS:
            lbl = ctk.CTkLabel(row1, text=f"{label}:")
            lbl.pack(side="left", padx=(5, 2))
            self.labels[key] = lbl
            entry = ctk.CTkEntry(row1, width=50)
            entry.insert(0, str(default))
            entry.pack(side="left", padx=(0, 5))
            self.entries[key] = entry

        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="w", pady=(6, 0))

        ctk.CTkLabel(row2, text="Tamanho da página:").pack(side="left", padx=(5, 5))
        self.size_menu = ctk.CTkOptionMenu(row2, width=150, values=["Manter original", "A4"])
        self.size_menu.set("Manter original")
        self.size_menu.pack(side="left", padx=(0, 15))

        self.mirror_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row2, text="Encadernação (margem interna espelhada)",
                        variable=self.mirror_var, command=self.on_mirror_change).pack(side="left", padx=5)

        self.grayscale_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row2, text="Converter coloridas em tons de cinza",
                        variable=self.grayscale_var).pack(side="left", padx=(15, 5))

        self.btn_preview = ctk.CTkButton(row2, text="Pré-visualizar", width=120, command=self.open_preview)
        self.btn_preview.pack(side="left", padx=(20, 5))

    def on_mirror_change(self):
        mirrored = self.mirror_var.get()
        self.labels["margin_left_mm"].configure(text="Interna:" if mirrored else "Esquerda:")
        self.labels["margin_right_mm"].configure(text="Externa:" if mirrored else "Direita:")

    def open_preview(self):
        tool_view = self.master
        files = getattr(tool_view, "file_list", None)
        files = files.files if files else []
        if not files:
            messagebox.showwarning("Aviso", "Selecione um PDF para pré-visualizar.")
            return
        try:
            values = self.get_values()
        except ValueError as e:
            messagebox.showerror("Valor Inválido", str(e))
            return
        MarginPreview(self.winfo_toplevel(), files[0], values)

    def _page_size(self):
        return "a4" if self.size_menu.get() == "A4" else "original"

    def get_values(self):
        values = {}
        for label, key, default in self.FIELDS:
            values[key] = parse_number(self.entries[key].get(), f"Margem {label}", default)

        if self._page_size() == "a4":
            if values["margin_left_mm"] + values["margin_right_mm"] >= self.A4_W_MM or                values["margin_top_mm"] + values["margin_bottom_mm"] >= self.A4_H_MM:
                raise ValueError("As margens são grandes demais para uma página A4 (210 x 297 mm).")

        values["page_size"] = self._page_size()
        values["grayscale"] = self.grayscale_var.get()
        values["mirror_margins"] = self.mirror_var.get()
        return values

    def load_values(self, values):
        # compatibilidade com configurações anteriores
        legacy = {"margin_left_mm": "margin_x_mm", "margin_right_mm": "margin_x_mm",
                  "margin_top_mm": "margin_y_mm", "margin_bottom_mm": "margin_y_mm"}
        for _, key, _ in self.FIELDS:
            source = key if key in values else legacy[key]
            if source in values:
                entry = self.entries[key]
                entry.delete(0, "end")
                entry.insert(0, format_number(float(values[source])))

        if values.get("page_size") == "a4":
            self.size_menu.set("A4")
        self.grayscale_var.set(bool(values.get("grayscale", False)))
        self.mirror_var.set(bool(values.get("mirror_margins", False)))
        self.on_mirror_change()

class PagesOptions(ctk.CTkFrame):
    MODES = (("Extrair (manter só estas)", "extract"), ("Remover estas páginas", "remove"))

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row1, text="Páginas:").pack(side="left", padx=(5, 5))
        self.entry = ctk.CTkEntry(row1, width=220, placeholder_text="ex.: 1-3, 7, 10-")
        self.entry.pack(side="left", padx=(0, 10))

        self.mode_var = ctk.StringVar(value="extract")
        for text, value in self.MODES:
            ctk.CTkRadioButton(row1, text=text, variable=self.mode_var, value=value).pack(side="left", padx=8)

        ctk.CTkLabel(self, text="A ordem digitada é respeitada, então \"3,1,2\" também reordena as páginas.",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray"
                     ).grid(row=1, column=0, sticky="w", padx=5, pady=(4, 0))

    def get_values(self):
        spec = self.entry.get().strip()
        if not spec:
            raise ValueError("Informe as páginas (ex.: 1-3, 7, 10-).")
        return {"pages_spec": spec, "mode": self.mode_var.get()}

    def load_values(self, values):
        if values.get("mode") in [v for _, v in self.MODES]:
            self.mode_var.set(values["mode"])


class ImposeOptions(ctk.CTkFrame):
    LAYOUTS = (("2 páginas por folha", "2up"), ("Livreto (dobra e grampo central)", "booklet"))

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="w")
        self.layout_var = ctk.StringVar(value="2up")
        for text, value in self.LAYOUTS:
            ctk.CTkRadioButton(row1, text=text, variable=self.layout_var, value=value,
                               command=self.on_layout_change).pack(side="left", padx=(5, 15))

        ctk.CTkLabel(row1, text="Folha:").pack(side="left", padx=(10, 5))
        self.sheet_menu = ctk.CTkOptionMenu(row1, width=170, values=["A4 paisagem", "Duas páginas lado a lado"])
        self.sheet_menu.set("A4 paisagem")
        self.sheet_menu.pack(side="left")

        self.lbl_hint = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12, slant="italic"), text_color="gray")
        self.lbl_hint.grid(row=1, column=0, sticky="w", padx=5, pady=(4, 0))
        self.on_layout_change()

    def on_layout_change(self):
        if self.layout_var.get() == "booklet":
            self.lbl_hint.configure(text="Imprima frente e verso (virar pela borda curta). "
                                         "As páginas são completadas até um múltiplo de 4.")
        else:
            self.lbl_hint.configure(text="Mantém a ordem de leitura: 1 e 2 na primeira folha, 3 e 4 na segunda.")

    def get_values(self):
        sheet = "auto" if self.sheet_menu.get().startswith("Duas") else "a4"
        return {"layout": self.layout_var.get(), "sheet_size": sheet}

    def load_values(self, values):
        if values.get("layout") in [v for _, v in self.LAYOUTS]:
            self.layout_var.set(values["layout"])
        if values.get("sheet_size") == "auto":
            self.sheet_menu.set("Duas páginas lado a lado")
        self.on_layout_change()


class PreflightOptions(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row1, text="Resolução mínima das imagens (dpi):").pack(side="left", padx=(5, 5))
        self.entry_dpi = ctk.CTkEntry(row1, width=60)
        self.entry_dpi.insert(0, "150")
        self.entry_dpi.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row1, text="Área de segurança da borda (mm):").pack(side="left", padx=(5, 5))
        self.entry_margin = ctk.CTkEntry(row1, width=60)
        self.entry_margin.insert(0, "5")
        self.entry_margin.pack(side="left")

        ctk.CTkLabel(self, text=f"Nada é alterado nos PDFs: o resultado é o relatório {pdf_tools.PREFLIGHT_REPORT_NAME}.",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray"
                     ).grid(row=1, column=0, sticky="w", padx=5, pady=(4, 0))

    def get_values(self):
        return {"min_dpi": parse_number(self.entry_dpi.get(), "Resolução mínima", 150),
                "safe_margin_mm": parse_number(self.entry_margin.get(), "Área de segurança", 5)}

    def load_values(self, values):
        for entry, key in ((self.entry_dpi, "min_dpi"), (self.entry_margin, "safe_margin_mm")):
            if key in values:
                entry.delete(0, "end")
                entry.insert(0, format_number(float(values[key])))


class ProtectOptions(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row1, text="Senha:").pack(side="left", padx=(5, 5))
        self.entry_pw = ctk.CTkEntry(row1, width=180, show="•")
        self.entry_pw.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(row1, text="Repetir:").pack(side="left", padx=(5, 5))
        self.entry_pw2 = ctk.CTkEntry(row1, width=180, show="•")
        self.entry_pw2.pack(side="left", padx=(0, 10))

        self.show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row1, text="Mostrar", variable=self.show_var, width=20,
                        command=self.toggle_show).pack(side="left", padx=5)

        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ctk.CTkLabel(row2, text="Permitir:").pack(side="left", padx=(5, 5))
        self.print_var = ctk.BooleanVar(value=True)
        self.copy_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(row2, text="Impressão", variable=self.print_var).pack(side="left", padx=8)
        ctk.CTkCheckBox(row2, text="Cópia de texto", variable=self.copy_var).pack(side="left", padx=8)
        ctk.CTkLabel(row2, text="(cada leitor de PDF decide se respeita essas restrições)",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray").pack(side="left", padx=10)

    def toggle_show(self):
        show = "" if self.show_var.get() else "•"
        self.entry_pw.configure(show=show)
        self.entry_pw2.configure(show=show)

    def get_values(self):
        pw = self.entry_pw.get()
        if not pw:
            raise ValueError("Informe uma senha.")
        if pw != self.entry_pw2.get():
            raise ValueError("As duas senhas não são iguais.")
        return {"password": pw, "allow_printing": self.print_var.get(), "allow_copy": self.copy_var.get()}

    # senha não é salva nas configurações por segurança


class UnlockOptions(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        ctk.CTkLabel(self, text="Senha atual:").pack(side="left", padx=(5, 5))
        self.entry_pw = ctk.CTkEntry(self, width=200, show="•")
        self.entry_pw.pack(side="left", padx=(0, 10))

        self.show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self, text="Mostrar", variable=self.show_var, width=20,
                        command=lambda: self.entry_pw.configure(show="" if self.show_var.get() else "•")
                        ).pack(side="left", padx=5)

        ctk.CTkLabel(self, text="Informe a senha atual do arquivo. Arquivos sem senha passam sem alteração.",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray").pack(side="left", padx=10)

    def get_values(self):
        return {"password": self.entry_pw.get()}


class GrayscaleOptions(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        ctk.CTkLabel(self, text="Resolução (dpi):").pack(side="left", padx=(5, 5))
        self.entry_dpi = ctk.CTkEntry(self, width=60)
        self.entry_dpi.insert(0, "200")
        self.entry_dpi.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(self, text="Páginas já em preto e branco são mantidas como estão, com o texto pesquisável.",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray").pack(side="left", padx=10)

    def get_values(self):
        dpi = parse_number(self.entry_dpi.get(), "Resolução", 200)
        if not 50 <= dpi <= 600:
            raise ValueError("A resolução deve ficar entre 50 e 600 dpi.")
        return {"dpi": int(dpi)}

    def load_values(self, values):
        if "dpi" in values:
            self.entry_dpi.delete(0, "end")
            self.entry_dpi.insert(0, format_number(float(values["dpi"])))


class MergeOptions(OutputFilenameOptions):
    """Opções para o arquivo mesclado com suporte a marcadores."""

    def __init__(self, master):
        super().__init__(master)
        self.bookmark_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self, text="Criar marcador para cada arquivo",
                        variable=self.bookmark_var).pack(side="left", padx=(20, 5))
        ctk.CTkLabel(self, text="(facilita navegar no PDF final)",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray").pack(side="left", padx=5)

    def get_values(self):
        values = super().get_values()
        values["add_bookmarks"] = self.bookmark_var.get()
        return values

    def load_values(self, values):
        self.bookmark_var.set(bool(values.get("add_bookmarks", True)))


class NumberPagesOptions(ctk.CTkFrame):
    FORMATS = {"1, 2, 3": "{n}", "1 de 10": "{n} de {total}",
               "Pág. 1": "Pág. {n}", "- 1 -": "- {n} -"}
    POSITIONS = {"Inferior centro": "inferior-centro", "Inferior direita": "inferior-direita",
                 "Inferior esquerda": "inferior-esquerda", "Superior centro": "superior-centro",
                 "Superior direita": "superior-direita", "Superior esquerda": "superior-esquerda"}

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(row1, text="Formato:").pack(side="left", padx=(5, 5))
        self.format_menu = ctk.CTkOptionMenu(row1, width=120, values=list(self.FORMATS))
        self.format_menu.set("1, 2, 3")
        self.format_menu.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row1, text="Posição:").pack(side="left", padx=(5, 5))
        self.position_menu = ctk.CTkOptionMenu(row1, width=150, values=list(self.POSITIONS))
        self.position_menu.set("Inferior centro")
        self.position_menu.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row1, text="Tamanho:").pack(side="left", padx=(5, 5))
        self.entry_size = ctk.CTkEntry(row1, width=50)
        self.entry_size.insert(0, "10")
        self.entry_size.pack(side="left")

        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="w", pady=(6, 0))

        ctk.CTkLabel(row2, text="Começar a numerar na página:").pack(side="left", padx=(5, 5))
        self.entry_first = ctk.CTkEntry(row2, width=50)
        self.entry_first.insert(0, "1")
        self.entry_first.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row2, text="contando a partir do número:").pack(side="left", padx=(5, 5))
        self.entry_start = ctk.CTkEntry(row2, width=50)
        self.entry_start.insert(0, "1")
        self.entry_start.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row2, text="Margem (mm):").pack(side="left", padx=(5, 5))
        self.entry_margin = ctk.CTkEntry(row2, width=50)
        self.entry_margin.insert(0, "10")
        self.entry_margin.pack(side="left")

        ctk.CTkLabel(self, text="Para pular a capa, comece na página 2 — ela não entra na contagem.",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray"
                     ).grid(row=2, column=0, sticky="w", padx=5, pady=(4, 0))

    def get_values(self):
        size = parse_number(self.entry_size.get(), "Tamanho", 10)
        if not 4 <= size <= 72:
            raise ValueError("O tamanho da fonte deve ficar entre 4 e 72.")
        first = parse_number(self.entry_first.get(), "Começar a numerar na página", 1)
        if first < 1:
            raise ValueError("A numeração precisa começar na página 1 ou depois.")
        return {"number_format": self.FORMATS[self.format_menu.get()],
                "position": self.POSITIONS[self.position_menu.get()],
                "font_size": size,
                "first_page": int(first),
                "start_at": int(parse_number(self.entry_start.get(), "Número inicial", 1)),
                "margin_mm": parse_number(self.entry_margin.get(), "Margem", 10)}

    def load_values(self, values):
        for rotulo, valor in self.FORMATS.items():
            if valor == values.get("number_format"):
                self.format_menu.set(rotulo)
        for rotulo, valor in self.POSITIONS.items():
            if valor == values.get("position"):
                self.position_menu.set(rotulo)
        for entry, key in ((self.entry_size, "font_size"), (self.entry_first, "first_page"),
                           (self.entry_start, "start_at"), (self.entry_margin, "margin_mm")):
            if key in values:
                entry.delete(0, "end")
                entry.insert(0, format_number(float(values[key])))


class WatermarkOptions(ctk.CTkFrame):
    PRESETS = ["CONFIDENCIAL", "CÓPIA", "RASCUNHO", "MINUTA", "URGENTE"]
    LAYOUTS = {"Diagonal": "diagonal", "Rodapé": "rodape"}
    OPACITIES = {"Bem clara": 0.08, "Clara": 0.15, "Média": 0.25, "Forte": 0.4}

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(row1, text="Texto:").pack(side="left", padx=(5, 5))
        self.combo = ctk.CTkComboBox(row1, width=190, values=self.PRESETS)
        self.combo.set("CONFIDENCIAL")
        self.combo.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row1, text="Posição:").pack(side="left", padx=(5, 5))
        self.layout_menu = ctk.CTkOptionMenu(row1, width=110, values=list(self.LAYOUTS))
        self.layout_menu.set("Diagonal")
        self.layout_menu.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row1, text="Cor:").pack(side="left", padx=(5, 5))
        self.color_menu = ctk.CTkOptionMenu(row1, width=110, values=list(pdf_tools.WATERMARK_COLORS))
        self.color_menu.set("Cinza")
        self.color_menu.pack(side="left")

        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="w", pady=(6, 0))

        ctk.CTkLabel(row2, text="Intensidade:").pack(side="left", padx=(5, 5))
        self.opacity_menu = ctk.CTkOptionMenu(row2, width=120, values=list(self.OPACITIES))
        self.opacity_menu.set("Clara")
        self.opacity_menu.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row2, text="Tamanho:").pack(side="left", padx=(5, 5))
        self.entry_size = ctk.CTkEntry(row2, width=50)
        self.entry_size.insert(0, "54")
        self.entry_size.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(row2, text="A marca fica sobre o conteúdo; textos longos diminuem para caber.",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray").pack(side="left", padx=5)

    def get_values(self):
        text = self.combo.get().strip()
        if not text:
            raise ValueError("Informe o texto da marca d'água.")
        size = parse_number(self.entry_size.get(), "Tamanho", 54)
        if not 8 <= size <= 200:
            raise ValueError("O tamanho da marca deve ficar entre 8 e 200.")
        return {"text": text, "layout": self.LAYOUTS[self.layout_menu.get()],
                "color": self.color_menu.get(), "font_size": size,
                "opacity": self.OPACITIES[self.opacity_menu.get()]}

    def load_values(self, values):
        if values.get("text"):
            self.combo.set(values["text"])
        for rotulo, valor in self.LAYOUTS.items():
            if valor == values.get("layout"):
                self.layout_menu.set(rotulo)
        if values.get("color") in pdf_tools.WATERMARK_COLORS:
            self.color_menu.set(values["color"])
        for rotulo, valor in self.OPACITIES.items():
            if valor == values.get("opacity"):
                self.opacity_menu.set(rotulo)
        if "font_size" in values:
            self.entry_size.delete(0, "end")
            self.entry_size.insert(0, format_number(float(values["font_size"])))


class MarginPreview(ctk.CTkToplevel):
    """Janela de pré-visualização das margens aplicadas."""
    MAX_SIDE = 560

    def __init__(self, master, pdf_path, values):
        super().__init__(master)
        self.title(f"Pré-visualização — {Path(pdf_path).name}")
        self.geometry("760x680")
        self.transient(master)
        self.values = values
        self.page = 0
        self.photo = None

        try:
            self.doc = pymupdf.open(str(pdf_path))
            if self.doc.needs_pass:
                raise ValueError("O PDF está protegido por senha.")
            if self.doc.page_count == 0:
                raise ValueError("O PDF não possui páginas.")
        except Exception as e:
            self.destroy()
            messagebox.showerror("Não foi possível abrir", str(e), parent=master)
            return

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.canvas.pack(expand=True, fill="both", padx=20, pady=(20, 10))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(0, 15))

        self.btn_prev = ctk.CTkButton(bar, text="←", width=40, command=lambda: self.step(-1))
        self.btn_prev.pack(side="left")
        self.lbl_page = ctk.CTkLabel(bar, text="")
        self.lbl_page.pack(side="left", padx=10)
        self.btn_next = ctk.CTkButton(bar, text="→", width=40, command=lambda: self.step(1))
        self.btn_next.pack(side="left")

        self.lbl_info = ctk.CTkLabel(bar, text="", text_color="gray", font=ctk.CTkFont(size=12))
        self.lbl_info.pack(side="left", padx=20)

        ctk.CTkButton(bar, text="Fechar", width=90, command=self.close).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<Escape>", lambda e: self.close())
        self.after(50, self.render)
        self.after(120, self.lift)
        self.after(140, self.focus_force)

    def step(self, delta):
        new = self.page + delta
        if 0 <= new < self.doc.page_count:
            self.page = new
            self.render()

    def render(self):
        MM_TO_PT = 2.83465
        v = self.values
        margins_pt = (v["margin_left_mm"] * MM_TO_PT, v["margin_right_mm"] * MM_TO_PT,
                      v["margin_top_mm"] * MM_TO_PT, v["margin_bottom_mm"] * MM_TO_PT)
        src = self.doc[self.page].rect

        try:
            w, h, box = pdf_tools.margin_box(src, self.page, margins_pt,
                                             v.get("page_size", "original"),
                                             v.get("mirror_margins", False))
        except ValueError as e:
            self.canvas.delete("all")
            self.canvas.create_text(20, 20, anchor="nw", width=max(200, self.canvas.winfo_width() - 40),
                                    text=f"As margens não cabem nesta página:\n{e}", fill="#D9534F")
            self.update_labels()
            return

        scale = min(self.MAX_SIDE / w, self.MAX_SIDE / h)
        sheet_w, sheet_h = int(w * scale), int(h * scale)
        content = pdf_tools._fit_centered(src, box)

        zoom = (content.width * scale) / src.width
        pix = self.doc[self.page].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
        sheet.paste(img, (int(content.x0 * scale), int(content.y0 * scale)))
        buf = io.BytesIO()
        sheet.save(buf, format="PPM")
        self.photo = tk.PhotoImage(master=self, data=buf.getvalue())

        self.canvas.delete("all")
        cw = self.canvas.winfo_width() or 700
        ch = self.canvas.winfo_height() or 560
        x, y = (cw - sheet_w) // 2, (ch - sheet_h) // 2
        dark = ctk.get_appearance_mode() == "Dark"
        self.canvas.configure(bg="#2B2B2B" if dark else "#C8C8C8")
        self.canvas.create_rectangle(x + 4, y + 4, x + sheet_w + 4, y + sheet_h + 4,
                                     fill="#0A0A0A" if dark else "#A8A8A8", outline="")
        self.canvas.create_image(x, y, anchor="nw", image=self.photo)
        self.canvas.create_rectangle(x, y, x + sheet_w, y + sheet_h, outline="#888")
        # linha tracejada da área útil
        self.canvas.create_rectangle(x + content.x0 * scale, y + content.y0 * scale,
                                     x + content.x1 * scale, y + content.y1 * scale,
                                     outline="#3B8ED0", dash=(4, 3))
        self.update_labels(w, h)

    def update_labels(self, w=None, h=None):
        MM_TO_PT = 2.83465
        self.lbl_page.configure(text=f"Página {self.page + 1} de {self.doc.page_count}")
        self.btn_prev.configure(state="normal" if self.page > 0 else "disabled")
        self.btn_next.configure(state="normal" if self.page < self.doc.page_count - 1 else "disabled")

        info = ""
        if w:
            info = f"Folha {w / MM_TO_PT:.0f} × {h / MM_TO_PT:.0f} mm"
            if self.values.get("mirror_margins"):
                lado = "esquerda" if self.page % 2 == 0 else "direita"
                info += f"  ·  lombada à {lado} (página {'ímpar' if self.page % 2 == 0 else 'par'})"
        self.lbl_info.configure(text=info)

    def close(self):
        try:
            self.doc.close()
        except Exception:
            pass
        self.destroy()


class VisualEditor(ctk.CTkFrame):
    """Editor visual de páginas para corte e rotação."""
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

        self.doc = pymupdf.open(stream=Path(input_file).read_bytes(), filetype="pdf")
        if self.doc.needs_pass:
            raise ValueError("O PDF está protegido por senha.")
        if self.doc.page_count == 0:
            raise ValueError("O PDF não possui páginas.")

        n = self.page_count = self.doc.page_count
        self.rotations = [0] * n
        self.crops = [None] * n
        self.thumb_photos = [None] * n
        self.thumb_rotations = [None] * n
        self.thumb_geometry = [None] * n
        self.thumb_cursor = 0
        self.current = 0
        self.zoom = None
        self.effective_zoom = 1.0
        self.page_photo = None
        self.image_box = None
        self.drag_start = None
        self.saving = False

        scaling = self._get_widget_scaling()
        self.base_px_per_pt = 96 / 72 * scaling
        self.thumb_w, self.thumb_h = int(96 * scaling), int(124 * scaling)
        self.thumb_canvas_w = self.thumb_w + int(28 * scaling)
        self.cell_h = self.thumb_h + int(34 * scaling)
        self.page_margin = int(16 * scaling)

        self.setup_ui()
        self.bind_keys()
        self.draw_thumb_placeholders()
        self.show_page(0)
        self.schedule_thumbs()

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
            for label, all_pages in (("Página:", False), ("Todas:", True)):
                ctk.CTkLabel(toolbar, text=label).pack(side="left", padx=(0, 4))
                ctk.CTkButton(toolbar, text="↺ Esq.", width=64, height=30, command=lambda a=all_pages: self.rotate(-90, all_pages=a)).pack(side="left", padx=(0, 4))
                ctk.CTkButton(toolbar, text="Dir. ↻", width=64, height=30, command=lambda a=all_pages: self.rotate(90, all_pages=a)).pack(side="left", padx=(0, 12))
        elif self.tool_name == "Cortar PDF":
            ctk.CTkButton(toolbar, text="Aplicar a Todas", width=120, height=30, command=self.apply_crop_to_all).pack(side="left", padx=(0, 4))
            ctk.CTkButton(toolbar, text="Limpar Corte", width=100, height=30, command=self.clear_crop).pack(side="left")

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
                if isinstance(event.widget, tk.Entry):
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
        top = self.thumbs.canvasy(0)
        height = self.thumbs.winfo_height()
        if height < self.cell_h:
            return
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
        """Renderiza miniaturas sob demanda."""
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
        px_per_pt = max(min(px_per_pt, 8000 / max(w_pt, h_pt)), 0.02)
        self.effective_zoom = px_per_pt / self.base_px_per_pt
        self.lbl_zoom.configure(text=f"{round(self.effective_zoom * 100)}%")

        x_view, y_view = canvas.xview()[0], canvas.yview()[0]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(px_per_pt, px_per_pt).prerotate(rotation))
        self.page_pix = pix
        self.page_photo = tk.PhotoImage(master=self, data=pix.tobytes("ppm"))
        self.page_photo_dim = None

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
        if event.state & 0x4:
            self.step_zoom(-step)
            return
        if event.state & 0x1:
            self.page_canvas.xview_scroll(step, "units")
            return
        top, bottom = self.page_canvas.yview()
        if top <= 0 and bottom >= 1:
            self.show_page(self.current + step)
        else:
            self.page_canvas.yview_scroll(step, "units")

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
            crop = None
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
        # destaca a área selecionada
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
        for i in range(self.page_count):
            self.set_crop(i, crop)
        self.draw_page_crop()

    def clear_crop(self):
        self.set_crop(self.current, None)
        self.draw_page_crop()

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

        lbl_title = ctk.CTkLabel(self, text="Selecione uma Ferramenta", font=ctk.CTkFont(size=26, weight="bold"))
        lbl_title.pack(pady=(20, 10))

        area = ctk.CTkScrollableFrame(self, fg_color="transparent")
        area.pack(expand=True, fill="both", padx=20, pady=(0, 15))

        for group_name, group in TOOL_GROUPS:
            ctk.CTkLabel(area, text=group_name.upper(), text_color="gray",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=12, pady=(12, 4))

            grid_frame = ctk.CTkFrame(area, fg_color="transparent")
            grid_frame.pack(fill="x")
            grid_frame.grid_columnconfigure((0, 1, 2), weight=1, uniform="card")

            for index, (name, (desc, action, extra)) in enumerate(group.items()):
                card = ctk.CTkFrame(grid_frame, corner_radius=10)
                card.grid(row=index // 3, column=index % 3, padx=8, pady=6, sticky="nsew")

                font_size = 14 if len(name) > 14 else 15
                btn = ctk.CTkButton(card, text=name, font=ctk.CTkFont(size=font_size, weight="bold"),
                                    command=lambda n=name: self.open_tool_callback(n),
                                    height=50, fg_color="transparent", text_color=("black", "white"),
                                    hover_color=("gray85", "gray25"))
                btn.pack(fill="x", pady=(10, 0), padx=10)

                lbl = ctk.CTkLabel(card, text=desc, font=ctk.CTkFont(size=12), text_color="gray", wraplength=190)
                lbl.pack(pady=(0, 10), padx=10)

TOOL_GROUPS = (
    ("Organizar", {
        "Juntar PDFs": ("Junte vários PDFs em um só", pdf_tools.merge_pdfs, MergeOptions),
        "Dividir PDF": ("Divida um PDF em partes", pdf_tools.split_pdfs, SplitOptions),
        "Páginas": ("Extraia, remova ou reordene páginas", pdf_tools.select_pages, PagesOptions),
        "Numerar Páginas": ("Insira números de página", pdf_tools.number_pages, NumberPagesOptions),
        "Girar PDF": ("Gire as páginas visualmente", None, None),
        "Cortar PDF": ("Corte as áreas visualmente", None, None),
    }),
    ("Converter", {
        "Imagens para PDF": ("Converta imagens em PDF", pdf_tools.images_to_pdf, OutputFilenameOptions),
        "PDF para Imagens": ("Extraia páginas como imagens", pdf_tools.pdf_to_images, None),
        "Tons de Cinza": ("Converta para cinza e economize toner", pdf_tools.grayscale_pdfs, GrayscaleOptions),
        "Comprimir": ("Reduza o tamanho do PDF", pdf_tools.compress_pdfs, CompressOptions),
    }),
    ("Impressão", {
        "Adicionar Margens": ("Adicione bordas brancas", pdf_tools.add_margins, MarginsOptions),
        "Montar Folhas": ("2 páginas por folha ou livreto", pdf_tools.impose_pdfs, ImposeOptions),
        "Verificar Impressão": ("Encontre problemas antes de imprimir", pdf_tools.preflight_check, PreflightOptions),
    }),
    ("Segurança", {
        "Proteger com Senha": ("Criptografe o PDF com senha", pdf_tools.protect_pdfs, ProtectOptions),
        "Remover Senha": ("Tire a proteção de PDFs cuja senha você tem", pdf_tools.unlock_pdfs, UnlockOptions),
        "Marca d'Água": ("Carimbe um texto sobre as páginas", pdf_tools.watermark_pdfs, WatermarkOptions),
        "Limpar Metadados": ("Remova autor e histórico do arquivo", pdf_tools.clean_metadata, None),
    }),
)

class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        # inicializa extensão tkdnd
        self.TkdndVersion = TkinterDnD._require(self)

        self.title("PDF Tools")
        self.geometry("1100x720")
        self.minsize(1000, 650)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # fila de comunicação com a thread principal
        self.ui_queue = queue.Queue()
        self.poll_ui_queue()

        if getattr(sys, 'frozen', False):
            base_path = Path(sys._MEIPASS)
        else:
            base_path = Path(__file__).parent

        icon_path = base_path / "PDF.ico"
        if icon_path.exists():
            self.iconbitmap(str(icon_path))

        self.tools = {}
        for _, group in TOOL_GROUPS:
            self.tools.update(group)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.sidebar_frame = ctk.CTkFrame(self, width=210, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_propagate(False)
        self.sidebar_frame.grid_rowconfigure(2, weight=1)
        self.sidebar_frame.grid_columnconfigure(0, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="PDF Tools", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.nav_buttons = {}
        self.nav_buttons["Início"] = self.make_nav_button("🏠  Início", self.show_home)
        self.nav_buttons["Início"].grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")

        self.nav_scroll = ctk.CTkScrollableFrame(self.sidebar_frame, fg_color="transparent", width=170)
        self.nav_scroll.grid(row=2, column=0, sticky="nsew", padx=(4, 0))
        for group_name, group in TOOL_GROUPS:
            ctk.CTkLabel(self.nav_scroll, text=group_name.upper(), text_color="gray",
                         font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=12, pady=(10, 2))
            for name in group:
                button = self.make_nav_button(name, lambda n=name: self.open_tool(n), master=self.nav_scroll)
                button.pack(fill="x", padx=4, pady=1)
                self.nav_buttons[name] = button

        self.theme_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.theme_frame.grid(row=3, column=0, padx=10, pady=(10, 20), sticky="s")

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

    def make_nav_button(self, text, command, master=None):
        return ctk.CTkButton(master or self.sidebar_frame, text=text, command=command, anchor="w", height=32,
                             fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray75", "gray30"))

    def set_active_nav(self, name):
        for key, button in self.nav_buttons.items():
            active = key == name
            button.configure(fg_color=("#3B8ED0", "#1F6AA5") if active else "transparent",
                             text_color=("white", "white") if active else ("gray10", "gray90"))

    def post(self, func):
        """Executa função na thread principal da interface."""
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
                pass
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
            return
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
            messagebox.showerror("Erro", f"Não foi possível abrir '{Path(input_file).name}':\n\n{e}")
            self.open_tool(tool_name, preset_files=[input_file], force=True)
            return
        self.current_view.pack(fill="both", expand=True)
        self.set_active_nav(tool_name)

if __name__ == "__main__":
    app = App()
    app.mainloop()
