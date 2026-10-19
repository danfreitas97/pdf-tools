import io
import math
import os
import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from pypdf import PdfWriter, PdfReader
from pypdf.constants import UserAccessPermissions
from PIL import Image, ImageOps, ImageChops
import pymupdf

class Cancelled(BaseException):
    """Exceção levantada quando a operação é cancelada pelo usuário."""

def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()

def format_size(num_bytes):
    for unit in ("B", "KB", "MB"):
        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}".replace(".", ",")
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB".replace(".", ",")

def _output_stems(input_files):
    """Gera nomes base únicos para os arquivos de saída."""
    taken = {Path(f).stem.lower() for f in input_files}
    seen = set()
    stems = []
    for f in input_files:
        stem = Path(f).stem
        if stem.lower() in seen:
            n = 2
            while f"{stem}_{n}".lower() in taken:
                n += 1
            stem = f"{stem}_{n}"
            taken.add(stem.lower())
        seen.add(stem.lower())
        stems.append(stem)
    return stems

def _free_path(path):
    """Nunca sobrescreve: se o arquivo já existe, usa 'nome (1).pdf', 'nome (2).pdf'..."""
    path = Path(path)
    candidate, n = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate

def _pages_to_bytes(reader, page_numbers):
    writer = PdfWriter()
    for p in page_numbers:
        writer.add_page(reader.pages[p])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()

def merge_pdfs(input_files, output_file, add_bookmarks=True, progress_callback=None, cancel_event=None):
    """Junta múltiplos arquivos PDF em um só."""
    writer = PdfWriter()
    errors = []
    total = len(input_files)
    for i, pdf in enumerate(input_files):
        _check_cancel(cancel_event)
        if progress_callback:
            progress_callback(i, total, f"Adicionando {Path(pdf).name}...")
        try:
            writer.append(str(pdf), outline_item=Path(pdf).stem if add_bookmarks else None)
        except Exception as e:
            errors.append(f"{Path(pdf).name}: {e}")

    if len(writer.pages) == 0:
        raise ValueError("Nenhum PDF válido para juntar.\n\n" + "\n".join(errors))

    _check_cancel(cancel_event)
    if progress_callback:
        progress_callback(total, total, "Salvando arquivo...")

    with open(output_file, "wb") as f:
        writer.write(f)
    writer.close()
    if progress_callback:
        progress_callback(total, total, f"Concluído! {Path(output_file).name} ({format_size(Path(output_file).stat().st_size)})")
    return errors

def split_pdfs(input_files, output_dir, split_mode="half", split_param=None, progress_callback=None, cancel_event=None):
    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Dividindo {pdf.name}...")

        try:
            reader = PdfReader(str(pdf))
            num_pages = len(reader.pages)
            if num_pages < 2 and split_mode != "select":
                errors.append(f"{pdf.name}: possui apenas {num_pages} página(s), não há o que dividir")
                continue

            if split_mode == "select":
                for label, pages in parse_split_groups(split_param, num_pages):
                    _check_cancel(cancel_event)
                    out_file = _free_path(Path(output_dir) / f"{stem}_pag_{label}.pdf")
                    out_file.write_bytes(_pages_to_bytes(reader, pages))
                continue

            if split_mode == "half":
                mid = num_pages // 2
                ranges = [range(0, mid), range(mid, num_pages)]

            elif split_mode == "pages":
                chunk_size = max(int(split_param or 1), 1)
                ranges = [range(start, min(start + chunk_size, num_pages)) for start in range(0, num_pages, chunk_size)]

            elif split_mode == "size":
                target_bytes = float(split_param or 1) * 1024 * 1024
                ranges = []
                start = 0
                while start < num_pages:
                    def fits(end):
                        _check_cancel(cancel_event)
                        return len(_pages_to_bytes(reader, range(start, end))) <= target_bytes
                    # Busca o maior número de páginas que cabe no tamanho limite
                    good, bad, step = start + 1, None, 1
                    while good < num_pages:
                        cand = min(good + step, num_pages)
                        if fits(cand):
                            good, step = cand, step * 2
                        else:
                            bad = cand
                            break
                    if bad is not None:
                        while bad - good > 1:
                            mid = (good + bad) // 2
                            if fits(mid):
                                good = mid
                            else:
                                bad = mid
                    ranges.append(range(start, good))
                    start = good
            else:
                raise ValueError(f"Modo de divisão desconhecido: {split_mode}")

            for part, page_range in enumerate(ranges, start=1):
                _check_cancel(cancel_event)
                out_file = _free_path(Path(output_dir) / f"{stem}_parte{part}.pdf")
                out_file.write_bytes(_pages_to_bytes(reader, page_range))

        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def _downsample_images(pdf_bytes, max_dpi, cancel_event=None):
    """Reduz a resolução das imagens que excederem o DPI limite."""
    changed = False
    done = set()
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            _check_cancel(cancel_event)
            for img in {(i[0], i[2], i[3]) for i in page.get_images(full=True)}:
                xref, px_w, px_h = img
                if xref in done:
                    continue
                done.add(xref)
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                shown = max(rects, key=lambda r: r.width * r.height)
                if shown.width < 1 or shown.height < 1:
                    continue

                dpi = min(px_w / (shown.width / 72), px_h / (shown.height / 72))
                if dpi <= max_dpi * 1.1:
                    continue

                try:
                    src = doc.extract_image(xref)
                    pil = Image.open(io.BytesIO(src["image"]))
                    if pil.mode in ("RGBA", "LA", "PA") or src.get("smask"):
                        continue  # ignora imagens com transparência
                    scale = max_dpi / dpi
                    new_size = (max(1, int(px_w * scale)), max(1, int(px_h * scale)))
                    pil = pil.convert("RGB" if pil.mode not in ("RGB", "L") else pil.mode)
                    pil = pil.resize(new_size, Image.LANCZOS)
                    buf = io.BytesIO()
                    pil.save(buf, format="JPEG", quality=85, optimize=True)
                    if len(buf.getvalue()) < len(src["image"]):
                        page.replace_image(xref, stream=buf.getvalue())
                        changed = True
                except Exception:
                    pass

        return doc.tobytes(garbage=4, deflate=True) if changed else pdf_bytes

def compress_pdfs(input_files, output_dir, compression_level="Média", max_dpi=0,
                  progress_callback=None, cancel_event=None):
    """Comprime arquivos PDF ajustando fluxo de dados e imagens."""
    quality_map = {
        "Muito Alta": 30,
        "Alta": 50,
        "Média": 70,
        "Baixa": 85,
        "Muito Baixa": 95
    }
    img_quality = quality_map.get(compression_level, 70)

    errors = []
    size_before = size_after = 0
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Comprimindo {pdf.name}...")

        try:
            source = pdf.read_bytes()
            if max_dpi:
                source = _downsample_images(source, max_dpi, cancel_event)

            writer = PdfWriter(clone_from=io.BytesIO(source))

            for page in writer.pages:
                _check_cancel(cancel_event)
                page.compress_content_streams()
                for img in page.images:
                    if len(img.data) <= 100_000:
                        continue
                    try:
                        obj = img.indirect_reference.get_object()
                        pil = img.image
                        if "/SMask" in obj or "/Mask" in obj or pil.mode in ("RGBA", "LA", "PA"):
                            continue  # ignora imagens com transparência
                        if pil.mode not in ("RGB", "L"):
                            pil = pil.convert("RGB")
                        buf = io.BytesIO()
                        pil.save(buf, format="JPEG", quality=img_quality, optimize=True)
                        if len(buf.getvalue()) < len(img.data):
                            img.replace(pil, quality=img_quality, optimize=True)
                    except Exception:
                        pass

            out_file = _free_path(Path(output_dir) / f"{stem}_comprimido.pdf")
            buf = io.BytesIO()
            writer.write(buf)
            original_size = pdf.stat().st_size
            if len(buf.getvalue()) < original_size:
                out_file.write_bytes(buf.getvalue())
            else:
                shutil.copyfile(pdf, out_file)
            size_before += original_size
            size_after += out_file.stat().st_size
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        if size_before and size_after < size_before:
            saved = (1 - size_after / size_before) * 100
            progress_callback(total, total, f"Concluído! {format_size(size_before)} → {format_size(size_after)} (−{saved:.0f}%)")
        elif size_before:
            progress_callback(total, total, f"Concluído! Não foi possível reduzir mais: o PDF já estava otimizado ({format_size(size_before)}).")
        else:
            progress_callback(total, total, "Concluído!")
    return errors

def images_to_pdf(input_files, output_file, progress_callback=None, cancel_event=None):
    """Converte lista de imagens em um arquivo PDF."""
    errors = []
    total = len(input_files)
    with pymupdf.open() as out_doc:
        for i, img_path in enumerate(input_files):
            _check_cancel(cancel_event)
            if progress_callback:
                progress_callback(i, total, f"Processando imagem {i+1} de {total}...")
            try:
                with Image.open(img_path) as src:
                    img = ImageOps.exif_transpose(src)  # corrige orientação EXIF
                if img.mode in ("RGBA", "P", "LA", "PA"):
                    img = img.convert("RGBA")
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1])
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                buf = io.BytesIO()
                img.save(buf, format="JPEG")
                page = out_doc.new_page(width=img.width * 72 / 150, height=img.height * 72 / 150)
                page.insert_image(page.rect, stream=buf.getvalue())
            except Exception as e:
                errors.append(f"{Path(img_path).name}: {e}")

        if out_doc.page_count == 0:
            raise ValueError("Nenhuma imagem válida encontrada.\n\n" + "\n".join(errors))

        _check_cancel(cancel_event)
        if progress_callback:
            progress_callback(total, total, "Salvando PDF...")
        out_doc.save(str(output_file), garbage=3, deflate=True)

    if progress_callback:
        progress_callback(total, total, f"Concluído! {Path(output_file).name} ({format_size(Path(output_file).stat().st_size)})")
    return errors

def _is_page_colored(page: pymupdf.Page) -> bool:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.2, 0.2), colorspace=pymupdf.csRGB)
    try:
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        r, g, b = img.split()
        diff_rg = ImageChops.difference(r, g).getextrema()[1]
        diff_rb = ImageChops.difference(r, b).getextrema()[1]
        diff_gb = ImageChops.difference(g, b).getextrema()[1]
        return max(diff_rg, diff_rb, diff_gb) > 25
    except Exception:
        return True

def _fit_centered(src_rect, box):
    """Ajusta e centraliza o retângulo proporcionalmente dentro da caixa."""
    scale = min(box.width / src_rect.width, box.height / src_rect.height)
    w, h = src_rect.width * scale, src_rect.height * scale
    x = box.x0 + (box.width - w) / 2
    y = box.y0 + (box.height - h) / 2
    return pymupdf.Rect(x, y, x + w, y + h)

def margin_box(src_rect, p_num, margins_pt, page_size="original", mirror=False):
    """Calcula as dimensões da folha e a área útil delimitada pelas margens."""
    A4_W, A4_H = 595.0, 842.0
    ml, mr, mt, mb = margins_pt
    if mirror and p_num % 2:
        ml, mr = mr, ml  # espelha margens em páginas pares

    if page_size == "a4":
        w, h = (A4_H, A4_W) if src_rect.width > src_rect.height else (A4_W, A4_H)
    else:
        w, h = src_rect.width, src_rect.height

    if ml + mr >= w or mt + mb >= h:
        raise ValueError(f"as margens não cabem na página {p_num + 1} "
                         f"({w / 2.83465:.0f} x {h / 2.83465:.0f} mm)")
    return w, h, pymupdf.Rect(ml, mt, w - mr, h - mb)

def add_margins(input_files, output_dir, margin_left_mm=15, margin_right_mm=15,
                margin_top_mm=5, margin_bottom_mm=5, grayscale=False, page_size="original",
                mirror_margins=False, progress_callback=None, cancel_event=None):
    """Adiciona margens às páginas dos arquivos PDF."""
    MM_TO_PT = 2.83465
    margins_pt = (margin_left_mm * MM_TO_PT, margin_right_mm * MM_TO_PT,
                  margin_top_mm * MM_TO_PT, margin_bottom_mm * MM_TO_PT)

    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Adicionando margens em {pdf.name}...")

        try:
            with pymupdf.open(str(pdf)) as doc, pymupdf.open() as out_doc:
                n_pages = doc.page_count
                for p_num in range(n_pages):
                    _check_cancel(cancel_event)
                    page = doc[p_num]
                    src = page.rect
                    w, h, box = margin_box(src, p_num, margins_pt, page_size, mirror_margins)
                    out_page = out_doc.new_page(width=w, height=h)

                    if grayscale and _is_page_colored(page):
                        pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
                        img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=75, optimize=True)
                        out_page.insert_image(_fit_centered(src, box), stream=buf.getvalue())
                    else:
                        out_page.show_pdf_page(_fit_centered(src, box), doc, p_num)

                    if progress_callback and n_pages > 20 and p_num % 10 == 9:
                        progress_callback(i, total, f"Adicionando margens em {pdf.name} ({p_num + 1}/{n_pages})...")

                out_file = _free_path(Path(output_dir) / f"{stem}_margens.pdf")
                out_doc.save(str(out_file), garbage=4, deflate=True)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def rotate_pdf_visual(input_file, output_dir, rotations_dict):
    pdf = Path(input_file)
    writer = PdfWriter(clone_from=str(pdf))
    for i, page in enumerate(writer.pages):
        angle = rotations_dict.get(i, 0)
        if angle != 0:
            page.rotate(angle)

    out_file = _free_path(Path(output_dir) / f"{pdf.stem}_rotacionado.pdf")
    with open(out_file, "wb") as f:
        writer.write(f)
    return out_file

def crop_pdf_visual(input_file, output_dir, crop_boxes_dict):
    pdf = Path(input_file)
    with pymupdf.open(str(pdf)) as doc:
        for i, box in crop_boxes_dict.items():
            if i < doc.page_count:
                page = doc[i]
                rel_x0, rel_y0, rel_x1, rel_y1 = box

                w = page.rect.width
                h = page.rect.height

                # Converte coordenadas relativas para a orientação original da página
                rect = pymupdf.Rect(rel_x0 * w, rel_y0 * h, rel_x1 * w, rel_y1 * h) * page.derotation_matrix
                rect = rect + (page.cropbox.x0, page.cropbox.y0, page.cropbox.x0, page.cropbox.y0)
                page.set_cropbox(rect & page.mediabox)

        out_file = _free_path(Path(output_dir) / f"{pdf.stem}_cortado.pdf")
        doc.save(str(out_file), garbage=4, deflate=True)
    return out_file

def _visible_rect(page, rel_box):
    """Converte coordenadas relativas da página como é vista para o sistema não rotacionado."""
    w, h = page.rect.width, page.rect.height
    rel_x0, rel_y0, rel_x1, rel_y1 = rel_box
    return pymupdf.Rect(rel_x0 * w, rel_y0 * h, rel_x1 * w, rel_y1 * h) * page.derotation_matrix

def _image_stream(image_path):
    """Lê a imagem já endireitada pela orientação EXIF, em formato aceito pelo PDF."""
    with Image.open(image_path) as im:
        if im.format in ("JPEG", "PNG") and im.getexif().get(0x0112, 1) == 1:
            return Path(image_path).read_bytes()
        oriented = ImageOps.exif_transpose(im)
        buf = io.BytesIO()
        oriented.save(buf, format="PNG")
        return buf.getvalue()

def insert_images_visual(input_file, output_dir, placements_dict):
    """Insere imagens nas áreas marcadas. placements_dict: página -> [(caminho_imagem, caixa_relativa)]."""
    pdf = Path(input_file)
    streams = {}
    with pymupdf.open(str(pdf)) as doc:
        xrefs = {}
        for i, placements in placements_dict.items():
            if i >= doc.page_count:
                continue
            page = doc[i]
            for image_path, box in placements:
                if image_path not in streams:
                    streams[image_path] = _image_stream(image_path)
                # reaproveita a mesma imagem embutida em todas as páginas
                xref = page.insert_image(_visible_rect(page, box), stream=streams[image_path],
                                         xref=xrefs.get(image_path, 0), rotate=page.rotation)
                xrefs[image_path] = xref

        out_file = _free_path(Path(output_dir) / f"{pdf.stem}_com_imagem.pdf")
        doc.save(str(out_file), garbage=4, deflate=True)
    return out_file

def erase_areas_visual(input_file, output_dir, areas_dict):
    """Apaga de fato o conteúdo das áreas marcadas e as pinta de branco. areas_dict: página -> [caixa_relativa]."""
    pdf = Path(input_file)
    with pymupdf.open(str(pdf)) as doc:
        for i, boxes in areas_dict.items():
            if i >= doc.page_count:
                continue
            page = doc[i]
            for box in boxes:
                page.add_redact_annot(_visible_rect(page, box), fill=(1, 1, 1))
            # remove texto, pixels de imagem e desenhos totalmente cobertos (o resto fica sob o branco)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)

        out_file = _free_path(Path(output_dir) / f"{pdf.stem}_apagado.pdf")
        doc.save(str(out_file), garbage=4, deflate=True)
    return out_file

def pdf_to_images(input_files, output_dir, progress_callback=None, cancel_event=None):
    """Converte páginas de PDF em imagens PNG."""
    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Extraindo imagens de {pdf.name}...")

        try:
            with pymupdf.open(str(pdf)) as doc:
                for page_num in range(len(doc)):
                    _check_cancel(cancel_event)
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=150)
                    out_file = _free_path(Path(output_dir) / f"{stem}_pag_{page_num+1}.png")
                    pix.save(str(out_file))
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def parse_page_ranges(spec, n_pages):
    """Converte especificação de páginas (ex.: '1-3, 5, 8-') em índices de páginas."""
    if not spec or not spec.strip():
        raise ValueError("Informe as páginas (ex.: 1-3, 5, 8-).")

    pages = []
    for part in spec.replace(";", ",").replace("(", "").replace(")", "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)?\s*[-–]\s*(\d+)?", part)
        if m and (m.group(1) or m.group(2)):
            start = int(m.group(1)) if m.group(1) else 1
            end = int(m.group(2)) if m.group(2) else n_pages
            if start > end:
                start, end = end, start
            if start < 1 or end > n_pages:
                raise ValueError(f"Intervalo '{part}' fora do documento, que tem {n_pages} páginas.")
            pages.extend(range(start - 1, end))
        elif part.isdigit():
            n = int(part)
            if not 1 <= n <= n_pages:
                raise ValueError(f"A página {n} não existe: o documento tem {n_pages} páginas.")
            pages.append(n - 1)
        else:
            raise ValueError(f"Trecho inválido: '{part}'. Use algo como 1-3, 5, 8-.")

    if not pages:
        raise ValueError("Nenhuma página selecionada.")
    return pages

def parse_split_groups(spec, n_pages):
    """Divide '5-10, (1, 5, 8, 10), 9' em grupos de páginas, um por arquivo de saída.
    Cada item separado por vírgula vira um arquivo; páginas entre parênteses ficam juntas no mesmo."""
    if not spec or not spec.strip():
        raise ValueError("Informe as páginas (ex.: 5-10, (1, 5, 8, 10), 9).")
    if re.search(r"\([^)]*\(|\)[^(]*\)", spec) or spec.count("(") != spec.count(")"):
        raise ValueError("Parênteses não fecham direito. Use algo como 5-10, (1, 5, 8, 10), 9.")

    item = r"\(([^()]*)\)|([^,;()]+)"
    leftover = re.sub(item, "", spec)
    if leftover.strip(" ,;"):
        raise ValueError(f"Trecho inválido: '{leftover.strip(' ,;')}'. Use algo como 5-10, (1, 5, 8, 10), 9.")

    groups = []
    for m in re.finditer(item, spec):
        text = m.group(1) if m.group(1) is not None else m.group(2)
        if not text.strip():
            continue
        pages = parse_page_ranges(text, n_pages)
        label = "_".join(re.sub(r"\s+", "", p.replace("–", "-")) for p in re.split(r"[,;]", text) if p.strip())
        groups.append((label[:60], pages))
    if not groups:
        raise ValueError("Nenhuma página selecionada.")
    return groups

def select_pages(input_files, output_dir, pages_spec="", mode="extract",
                 progress_callback=None, cancel_event=None):
    """Extrai ou remove as páginas especificadas dos arquivos PDF."""
    suffix = "paginas" if mode == "extract" else "sem_paginas"
    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Selecionando páginas de {pdf.name}...")

        try:
            reader = PdfReader(str(pdf))
            n = len(reader.pages)
            chosen = parse_page_ranges(pages_spec, n)

            if mode == "remove":
                drop = set(chosen)
                chosen = [j for j in range(n) if j not in drop]
                if not chosen:
                    raise ValueError("isso removeria todas as páginas do documento")

            writer = PdfWriter()
            for j in chosen:
                _check_cancel(cancel_event)
                writer.add_page(reader.pages[j])

            out_file = _free_path(Path(output_dir) / f"{stem}_{suffix}.pdf")
            with open(out_file, "wb") as fh:
                writer.write(fh)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def _booklet_order(n_sheets_pages):
    """Calcula a ordem das páginas para imposição em livreto."""
    order = []
    left, right = n_sheets_pages, 1
    while right < left:
        order.extend([left, right, right + 1, left - 1])
        left -= 2
        right += 2
    return order

def impose_pdfs(input_files, output_dir, layout="2up", sheet_size="a4",
                progress_callback=None, cancel_event=None):
    """Monta 2 páginas por folha ou organiza em livreto."""
    A4_W, A4_H = 595.0, 842.0
    suffix = "livreto" if layout == "booklet" else "2em1"
    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Montando {pdf.name}...")

        try:
            with pymupdf.open(str(pdf)) as doc, pymupdf.open() as out_doc:
                n = doc.page_count
                if n == 0:
                    raise ValueError("o documento não tem páginas")

                if layout == "booklet":
                    padded = n + (-n % 4)  # ajusta para múltiplo de 4
                    slots = [p if p <= n else None for p in _booklet_order(padded)]
                else:
                    slots = list(range(1, n + 1)) + ([None] if n % 2 else [])

                first = doc[0].rect
                if sheet_size == "a4":
                    sheet_w, sheet_h = A4_H, A4_W
                else:
                    sheet_w, sheet_h = first.width * 2, first.height

                for k in range(0, len(slots), 2):
                    _check_cancel(cancel_event)
                    sheet = out_doc.new_page(width=sheet_w, height=sheet_h)
                    halves = (pymupdf.Rect(0, 0, sheet_w / 2, sheet_h),
                              pymupdf.Rect(sheet_w / 2, 0, sheet_w, sheet_h))
                    for half, page_no in zip(halves, slots[k:k + 2]):
                        if page_no is None:
                            continue
                        src = doc[page_no - 1]
                        sheet.show_pdf_page(_fit_centered(src.rect, half), doc, page_no - 1)

                out_file = _free_path(Path(output_dir) / f"{stem}_{suffix}.pdf")
                out_doc.save(str(out_file), garbage=4, deflate=True)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

PREFLIGHT_REPORT_NAME = "verificacao_pre_impressao.txt"

def _short_pages(pages, limit=8):
    pages = sorted(set(pages))
    shown = ", ".join(str(p) for p in pages[:limit])
    return f"pág. {shown}" + (f" e mais {len(pages) - limit}" if len(pages) > limit else "")

def _page_content_bbox(page):
    """Calcula a caixa delimitadora de todo o conteúdo da página."""
    box = None
    for block in page.get_text("blocks"):
        r = pymupdf.Rect(block[:4])
        box = r if box is None else box | r
    for xref in {img[0] for img in page.get_images(full=True)}:
        for r in page.get_image_rects(xref):
            box = r if box is None else box | r
    for drawing in page.get_drawings():
        r = drawing["rect"]
        if not r.is_empty:
            box = r if box is None else box | r
    return box

def preflight_check(input_files, output_dir, min_dpi=150, safe_margin_mm=5,
                    progress_callback=None, cancel_event=None):
    """Gera relatório de verificação pré-impressão dos PDFs."""
    MM_TO_PT = 2.83465
    safe_pt = safe_margin_mm * MM_TO_PT
    errors = []
    lines = ["VERIFICAÇÃO PRÉ-IMPRESSÃO", "=" * 60, ""]
    total_warnings = 0
    total = len(input_files)

    for i, pdf_path in enumerate(input_files):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Verificando {pdf.name}...")

        lines.append(pdf.name)
        try:
            with pymupdf.open(str(pdf)) as doc:
                if doc.needs_pass:
                    raise ValueError("protegido por senha")

                sizes = {}
                low_dpi = []
                loose_fonts = set()
                near_edge = []
                blank = []

                for page in doc:
                    _check_cancel(cancel_event)
                    n = page.number + 1
                    r = page.rect
                    key = (round(r.width / MM_TO_PT), round(r.height / MM_TO_PT))
                    sizes.setdefault(key, []).append(n)

                    for img in {(i[0], i[2], i[3]) for i in page.get_images(full=True)}:
                        xref, px_w, px_h = img
                        for rect in page.get_image_rects(xref):
                            if rect.width > 1 and rect.height > 1:
                                dpi = min(px_w / (rect.width / 72), px_h / (rect.height / 72))
                                if dpi < min_dpi:
                                    low_dpi.append((n, round(dpi)))

                    for font in page.get_fonts(full=True):
                        if font[1] == "n/a":  # fonte não embutida
                            loose_fonts.add(font[3])

                    content = _page_content_bbox(page)
                    if content is None:
                        blank.append(n)
                    elif (content.x0 < r.x0 + safe_pt or content.y0 < r.y0 + safe_pt or
                          content.x1 > r.x1 - safe_pt or content.y1 > r.y1 - safe_pt):
                        near_edge.append(n)

                warnings = []
                if len(sizes) > 1:
                    desc = "; ".join(f"{w}x{h}mm em {len(p)} pág." for (w, h), p in sizes.items())
                    warnings.append(f"Tamanhos de página misturados: {desc}")
                if low_dpi:
                    worst = min(d for _, d in low_dpi)
                    warnings.append(f"{len(low_dpi)} imagem(ns) abaixo de {min_dpi} dpi "
                                    f"(pior caso {worst} dpi) — {_short_pages(p for p, _ in low_dpi)}")
                if loose_fonts:
                    warnings.append(f"{len(loose_fonts)} fonte(s) não embutida(s): "
                                    f"{', '.join(sorted(loose_fonts)[:5])}")
                if near_edge:
                    warnings.append(f"Conteúdo a menos de {safe_margin_mm}mm da borda — "
                                    f"{_short_pages(near_edge)}")
                if blank:
                    warnings.append(f"Página(s) em branco — {_short_pages(blank)}")

                lines.append(f"  {doc.page_count} páginas")
                if warnings:
                    total_warnings += len(warnings)
                    lines.extend(f"  [!] {w}" for w in warnings)
                else:
                    lines.append("  [ok] Nenhum problema encontrado.")
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")
            lines.append(f"  [erro] Não foi possível ler: {e}")
        lines.append("")

    report = Path(output_dir) / PREFLIGHT_REPORT_NAME
    try:
        report.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        errors.append(f"{PREFLIGHT_REPORT_NAME}: {e}")

    if progress_callback:
        if total_warnings:
            progress_callback(total, total, f"Concluído! {total_warnings} aviso(s) — veja {PREFLIGHT_REPORT_NAME}")
        else:
            progress_callback(total, total, f"Concluído! Nenhum problema encontrado — relatório em {PREFLIGHT_REPORT_NAME}")
    return errors

def protect_pdfs(input_files, output_dir, password="", allow_printing=True, allow_copy=True,
                 progress_callback=None, cancel_event=None):
    """Protege arquivos PDF com senha e permissões de acesso."""
    if not password:
        raise ValueError("Informe uma senha.")

    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Protegendo {pdf.name}...")

        try:
            writer = PdfWriter(clone_from=str(pdf))
            perms = UserAccessPermissions.all()
            if not allow_printing:
                perms &= ~UserAccessPermissions.PRINT
                perms &= ~UserAccessPermissions.PRINT_TO_REPRESENTATION
            if not allow_copy:
                perms &= ~UserAccessPermissions.EXTRACT
                perms &= ~UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS
            writer.encrypt(user_password=password, permissions_flag=perms, algorithm="AES-256")

            out_file = _free_path(Path(output_dir) / f"{stem}_protegido.pdf")
            with open(out_file, "wb") as fh:
                writer.write(fh)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def unlock_pdfs(input_files, output_dir, password="", progress_callback=None, cancel_event=None):
    """Remove a proteção por senha de arquivos PDF."""
    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Removendo senha de {pdf.name}...")

        try:
            reader = PdfReader(str(pdf))
            if reader.is_encrypted and not reader.decrypt(password):
                raise ValueError("senha incorreta")

            writer = PdfWriter()
            writer.append(reader)
            out_file = _free_path(Path(output_dir) / f"{stem}_sem_senha.pdf")
            with open(out_file, "wb") as fh:
                writer.write(fh)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def clean_metadata(input_files, output_dir, progress_callback=None, cancel_event=None):
    """Remove metadados dos arquivos PDF."""
    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Limpando metadados de {pdf.name}...")

        try:
            with pymupdf.open(str(pdf)) as doc:
                if doc.needs_pass:
                    raise ValueError("protegido por senha")
                doc.del_xml_metadata()
                doc.set_metadata({})
                out_file = _free_path(Path(output_dir) / f"{stem}_sem_metadados.pdf")
                doc.save(str(out_file), garbage=4, deflate=True)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def grayscale_pdfs(input_files, output_dir, dpi=200, progress_callback=None, cancel_event=None):
    """Converte páginas coloridas para escala de cinza."""
    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Convertendo {pdf.name}...")

        try:
            with pymupdf.open(str(pdf)) as doc, pymupdf.open() as out_doc:
                n_pages = doc.page_count
                for p_num in range(n_pages):
                    _check_cancel(cancel_event)
                    page = doc[p_num]
                    if not _is_page_colored(page):
                        out_doc.insert_pdf(doc, from_page=p_num, to_page=p_num)
                        continue

                    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
                    img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=80, optimize=True)
                    out_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
                    out_page.insert_image(out_page.rect, stream=buf.getvalue())

                    if progress_callback and n_pages > 20 and p_num % 10 == 9:
                        progress_callback(i, total, f"Convertendo {pdf.name} ({p_num + 1}/{n_pages})...")

                out_file = _free_path(Path(output_dir) / f"{stem}_cinza.pdf")
                out_doc.save(str(out_file), garbage=4, deflate=True)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

NUMBER_POSITIONS = ("inferior-centro", "inferior-direita", "inferior-esquerda",
                    "superior-centro", "superior-direita", "superior-esquerda")

def _number_point(page_rect, position, text_width, font_size, margin_pt):
    """Calcula a posição do número da página conforme o alinhamento escolhido."""
    top = position.startswith("superior")
    y = page_rect.y0 + margin_pt + font_size if top else page_rect.y1 - margin_pt

    if position.endswith("centro"):
        x = page_rect.x0 + (page_rect.width - text_width) / 2
    elif position.endswith("direita"):
        x = page_rect.x1 - margin_pt - text_width
    else:
        x = page_rect.x0 + margin_pt
    return pymupdf.Point(x, y)

def number_pages(input_files, output_dir, number_format="{n}", position="inferior-centro",
                 start_at=1, first_page=1, font_size=10, margin_mm=10,
                 progress_callback=None, cancel_event=None):
    """Insere numeração de páginas nos arquivos PDF."""
    MM_TO_PT = 2.83465
    margin_pt = margin_mm * MM_TO_PT
    errors = []
    total_files = len(input_files)

    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total_files, f"Numerando {pdf.name}...")

        try:
            with pymupdf.open(str(pdf)) as doc:
                if doc.needs_pass:
                    raise ValueError("protegido por senha")
                if first_page > doc.page_count:
                    raise ValueError(f"a numeração começaria na página {first_page}, "
                                     f"mas o documento tem {doc.page_count}")

                last_number = start_at + doc.page_count - first_page
                for p_num in range(first_page - 1, doc.page_count):
                    _check_cancel(cancel_event)
                    page = doc[p_num]
                    n = start_at + p_num - (first_page - 1)
                    try:
                        text = number_format.format(n=n, total=last_number)
                    except (KeyError, IndexError, ValueError):
                        raise ValueError(f"formato inválido: '{number_format}'. Use {{n}} e {{total}}.")

                    width = pymupdf.get_text_length(text, fontname="helv", fontsize=font_size)
                    point = _number_point(page.rect, position, width, font_size, margin_pt)
                    page.insert_text(point, text, fontname="helv", fontsize=font_size, color=(0, 0, 0))

                out_file = _free_path(Path(output_dir) / f"{stem}_numerado.pdf")
                doc.save(str(out_file), garbage=4, deflate=True)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total_files, total_files, "Concluído!")
    return errors

WATERMARK_COLORS = {"Cinza": (0.5, 0.5, 0.5), "Vermelho": (0.8, 0.1, 0.1), "Azul": (0.1, 0.3, 0.7)}

def watermark_pdfs(input_files, output_dir, text="CONFIDENCIAL", layout="diagonal",
                   font_size=54, opacity=0.15, color="Cinza",
                   progress_callback=None, cancel_event=None):
    """Aplica marca d'água de texto nas páginas dos PDFs."""
    if not text or not text.strip():
        raise ValueError("Informe o texto da marca d'água.")
    text = text.strip()
    rgb = WATERMARK_COLORS.get(color, WATERMARK_COLORS["Cinza"])

    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Aplicando marca d'água em {pdf.name}...")

        try:
            with pymupdf.open(str(pdf)) as doc:
                if doc.needs_pass:
                    raise ValueError("protegido por senha")

                for p_num in range(doc.page_count):
                    _check_cancel(cancel_event)
                    page = doc[p_num]
                    rect = page.rect

                    if layout == "rodape":
                        size = min(font_size, 24)
                        width = pymupdf.get_text_length(text, fontname="hebo", fontsize=size)
                        point = pymupdf.Point(rect.x0 + (rect.width - width) / 2, rect.y1 - 20)
                        writer = pymupdf.TextWriter(rect)
                        writer.append(point, text, fontsize=size, font=pymupdf.Font("hebo"))
                        writer.write_text(page, color=rgb, opacity=opacity)
                    else:
                        # Reduz tamanho da fonte se ultrapassar a diagonal
                        size = font_size
                        diagonal = (rect.width ** 2 + rect.height ** 2) ** 0.5
                        while size > 8 and pymupdf.get_text_length(text, fontname="hebo", fontsize=size) > diagonal * 0.8:
                            size -= 2

                        width = pymupdf.get_text_length(text, fontname="hebo", fontsize=size)
                        centre = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
                        start = pymupdf.Point(centre.x - width / 2, centre.y + size / 3)

                        writer = pymupdf.TextWriter(rect)
                        writer.append(start, text, fontsize=size, font=pymupdf.Font("hebo"))
                        # Rotaciona o texto ao longo da diagonal
                        angle = math.degrees(math.atan2(rect.height, rect.width))
                        writer.write_text(page, color=rgb, opacity=opacity,
                                          morph=(centre, pymupdf.Matrix(angle)))

                out_file = _free_path(Path(output_dir) / f"{stem}_marca.pdf")
                doc.save(str(out_file), garbage=4, deflate=True)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

PDFA_REPORT_NAME = "relatorio_pdfa.txt"

def _srgb_icc():
    """Perfil de cor sRGB. O do Windows é ICC v2, aceito por todas as versões de PDF/A."""
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/spool/drivers/color/sRGB Color Space Profile.icm"
    try:
        return windows.read_bytes()
    except OSError:
        from PIL import ImageCms
        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()

def _xmp_date(pdf_date):
    """Converte data no formato do PDF (D:20240131120000-03'00') para o formato do XMP."""
    m = re.match(r"(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+-])?(\d{2})?'?(\d{2})?", pdf_date or "")
    if not m:
        return None
    year, month, day, hour, minute, second, tz, tz_h, tz_m = m.groups()
    text = year + (f"-{month}" if month else "") + (f"-{day}" if month and day else "")
    if day and hour:
        text += f"T{hour}:{minute or '00'}" + (f":{second}" if second else "")
        if tz in ("Z", "z"):
            text += "Z"
        elif tz:
            text += f"{tz}{tz_h or '00'}:{tz_m or '00'}"
    return text

def _pdfa_xmp(meta, part):
    """Monta os metadados XMP que identificam o PDF/A, espelhando o dicionário Info do arquivo."""
    def clean(value):
        return xml_escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value))

    fields = []
    if meta.get("title"):
        fields.append(f'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{clean(meta["title"])}</rdf:li></rdf:Alt></dc:title>')
    if meta.get("author"):
        fields.append(f'<dc:creator><rdf:Seq><rdf:li>{clean(meta["author"])}</rdf:li></rdf:Seq></dc:creator>')
    if meta.get("subject"):
        fields.append(f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{clean(meta["subject"])}</rdf:li></rdf:Alt></dc:description>')
    if meta.get("keywords"):
        fields.append(f'<pdf:Keywords>{clean(meta["keywords"])}</pdf:Keywords>')
    if meta.get("producer"):
        fields.append(f'<pdf:Producer>{clean(meta["producer"])}</pdf:Producer>')
    if meta.get("creator"):
        fields.append(f'<xmp:CreatorTool>{clean(meta["creator"])}</xmp:CreatorTool>')
    for key, tag in (("creationDate", "xmp:CreateDate"), ("modDate", "xmp:ModifyDate")):
        date = _xmp_date(meta.get(key))
        if date:
            fields.append(f"<{tag}>{date}</{tag}>")

    body = "".join(f"   {f}\n" for f in fields)
    return (f'<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            f'<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
            f' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            f'  <rdf:Description rdf:about=""\n'
            f'    xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/"\n'
            f'    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
            f'    xmlns:pdf="http://ns.adobe.com/pdf/1.3/"\n'
            f'    xmlns:xmp="http://ns.adobe.com/xap/1.0/">\n'
            f'   <pdfaid:part>{part}</pdfaid:part>\n'
            f'   <pdfaid:conformance>B</pdfaid:conformance>\n'
            f'{body}'
            f'  </rdf:Description>\n'
            f' </rdf:RDF>\n'
            f'</x:xmpmeta>\n'
            f'<?xpacket end="w"?>').encode("utf-8")

def _certification_level(path):
    """Nível de permissão (P) de um documento certificado, ou None se não for certificado."""
    try:
        root = PdfReader(str(path)).trailer["/Root"]
        for ref in root["/Perms"]["/DocMDP"].get("/Reference", []):
            params = ref.get_object().get("/TransformParams")
            if params is not None:
                return int(params.get_object().get("/P", 2))
    except Exception:
        pass
    return None

def _pdfa_issues(doc, part):
    """Lista o que, no conteúdo original, impede a conformidade e não pode ser corrigido sem regravá-lo."""
    issues = []
    fonts = set()
    for page in doc:
        for font in page.get_fonts(full=True):
            if font[1] == "n/a" and font[2] != "Type3":
                fonts.add(font[3])
    if fonts:
        issues.append(f"{len(fonts)} fonte(s) não embutida(s): {', '.join(sorted(fonts)[:5])}")

    objects = []
    for xref in range(1, doc.xref_length()):
        try:
            objects.append(doc.xref_object(xref, compressed=True))
        except Exception:
            pass
    objects = "\n".join(objects)

    checks = [(r"/S\s*/(JavaScript|Launch|Sound|Movie|ResetForm|ImportData|Rendition)",
               "contém scripts ou ações (JavaScript, abrir programas, sons) que o PDF/A proíbe"),
              (r"/LZWDecode", "usa compressão LZW, proibida no PDF/A"),
              (r"/DeviceCMYK", "usa cores CMYK sem perfil de cor (o perfil adicionado é RGB)")]
    if part == 1:
        checks += [(r"/SMask\s+\d|/S\s*/Transparency|/(CA|ca)\s+(0|0?\.\d+)\b",
                    "usa transparência, proibida no PDF/A-1 (o PDF/A-2b aceita)"),
                   (r"/JPXDecode", "usa imagens JPEG 2000, proibidas no PDF/A-1 (o PDF/A-2b aceita)"),
                   (r"/Type\s*/(ObjStm|XRef)", "usa estrutura compactada do PDF 1.5, proibida no PDF/A-1 (o PDF/A-2b aceita)")]
    issues.extend(msg for pattern, msg in checks if re.search(pattern, objects))

    if doc.embfile_count() and part != 3:
        issues.append(f"tem {doc.embfile_count()} arquivo(s) anexado(s): use PDF/A-3b, que permite anexos")
    if "/ID" not in doc.pdf_trailer(compressed=True):
        issues.append("não tem identificador de arquivo (/ID), exigido pelo PDF/A")
    return issues

def _add_pdfa_info(doc, part, icc):
    """Adiciona metadados XMP e perfil de cor de saída, as duas peças que identificam um PDF/A."""
    def new_object(source):
        xref = doc.get_new_xref()
        doc.update_object(xref, source)
        return xref

    meta_xref = new_object("<< /Type /Metadata /Subtype /XML >>")
    doc.update_stream(meta_xref, _pdfa_xmp(doc.metadata or {}, part), new=True, compress=False)
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "Metadata", f"{meta_xref} 0 R")

    kind, intents = doc.xref_get_key(cat, "OutputIntents")
    if kind == "xref":
        intents = doc.xref_object(int(intents.split()[0]), compressed=True)
        kind = "array" if intents.startswith("[") else "null"
    refs = re.findall(r"(\d+)\s+0\s+R", intents) if kind == "array" else []
    if any(doc.xref_get_key(int(r), "S")[1] == "/GTS_PDFA1" for r in refs):
        return  # já tem perfil de saída para PDF/A

    icc_xref = new_object("<< /N 3 >>")
    doc.update_stream(icc_xref, icc, new=True)
    intent = new_object("<< /Type /OutputIntent /S /GTS_PDFA1 /OutputConditionIdentifier (sRGB IEC61966-2.1) "
                        f"/Info (sRGB IEC61966-2.1) /DestOutputProfile {icc_xref} 0 R >>")
    doc.xref_set_key(cat, "OutputIntents", "[" + " ".join(f"{r} 0 R" for r in refs + [str(intent)]) + "]")

def convert_pdfa(input_files, output_dir, part=2, progress_callback=None, cancel_event=None):
    """Converte para PDF/A sem regravar o conteúdo: o original é mantido byte a byte e as
    informações de PDF/A são anexadas ao final (atualização incremental), preservando assinaturas."""
    icc = _srgb_icc()
    errors = []
    lines = [f"CONVERSÃO PARA PDF/A-{part}b", "=" * 60, ""]
    warned = []
    signed = 0
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Convertendo {pdf.name}...")

        out_file = None
        try:
            original = pdf.read_bytes()
            if _certification_level(pdf) == 1:
                raise ValueError("é um documento certificado que não permite nenhuma alteração; "
                                 "qualquer acréscimo invalidaria a certificação")

            out_file = _free_path(Path(output_dir) / f"{stem}_pdfa.pdf")
            out_file.write_bytes(original)
            issues = []
            with pymupdf.open(str(out_file)) as doc:
                if doc.is_encrypted:
                    raise ValueError("o PDF/A não permite arquivos protegidos por senha ou criptografados")
                is_signed = doc.get_sigflags() > 0
                n_pages = doc.page_count
                declared = re.search(r"pdfaid:part\W{0,3}(\d)", doc.get_xml_metadata() or "")
                if declared:
                    status = f"já estava declarado como PDF/A-{declared.group(1)}; copiado sem alterações"
                    if int(declared.group(1)) != part:
                        issues.append(f"já era PDF/A-{declared.group(1)} e foi mantido assim, não virou PDF/A-{part}b "
                                      "(mudar a versão exigiria regravar o documento)")
                else:
                    if not doc.can_save_incrementally():
                        raise ValueError("a estrutura interna do arquivo está danificada e precisaria ser "
                                         "regravada, o que alteraria o documento")
                    issues = _pdfa_issues(doc, part)
                    _add_pdfa_info(doc, part, icc)
                    doc.saveIncr()
                    status = f"convertido para PDF/A-{part}b"

            # garantia: o arquivo original precisa continuar intacto no início do novo
            if not out_file.read_bytes().startswith(original):
                raise ValueError("a conversão alteraria o conteúdo original")
            _verify_pdfa_output(out_file, n_pages, declared.group(1) if declared else str(part), check_intent=not declared)

            signed += is_signed
            if is_signed:
                status += "; assinatura digital preservada"
                if not declared:
                    issues.append("tem assinatura digital: ela continua válida, mas validadores rigorosos podem "
                                  "apontar que houve um acréscimo depois da assinatura (a própria conversão)")
            lines.append(pdf.name)
            lines.append(f"  [ok] {status}")
            if issues:
                warned.append((pdf.name, issues))
                lines.append("  Atenção:")
                lines.extend(f"  [!] {issue}" for issue in issues)
            lines.append("")
        except Exception as e:
            if out_file:
                out_file.unlink(missing_ok=True)
            errors.append(f"{pdf.name}: {e} — nenhum arquivo foi gerado")

    warning = None
    if warned:
        try:
            (Path(output_dir) / PDFA_REPORT_NAME).write_text("\n".join(lines), encoding="utf-8")
            where = f"Os detalhes foram salvos em {PDFA_REPORT_NAME}, na pasta de destino."
        except OSError as e:
            where = f"Não foi possível salvar {PDFA_REPORT_NAME}: {e}"
        details = []
        for name, issues in warned:
            details.append(name)
            details.extend(f"   • {issue}" for issue in issues)
        if len(details) > 18:
            details = details[:18] + ["   … e mais itens no relatório"]
        warning = (f"{len(warned)} arquivo(s) foram gerados, mas verifique antes de enviar:\n\n"
                   + "\n".join(details)
                   + "\n\nItens como fontes não embutidas ou scripts fazem um validador de PDF/A recusar o arquivo. "
                     "Corrigi-los exigiria regravar o conteúdo, o que invalidaria assinaturas digitais.\n\n" + where)

    if progress_callback:
        done = total - len(errors)
        msg = f"Concluído! {done} de {total} arquivo(s) em PDF/A"
        if signed:
            msg += f", {signed} com assinatura preservada"
        if warned:
            msg += f" — {len(warned)} com avisos"
        progress_callback(total, total, msg)
    return {"errors": errors, "warning": warning}

def _verify_pdfa_output(out_file, n_pages, part, check_intent=True):
    """Reabre o arquivo gerado e confere se ele abre, tem as mesmas páginas e está declarado como PDF/A."""
    try:
        with pymupdf.open(str(out_file)) as doc:
            ok_pages = doc.page_count == n_pages
            ok_meta = re.search(rf"pdfaid:part\W{{0,3}}{part}", doc.get_xml_metadata() or "") is not None
            ok_intent = not check_intent or "GTS_PDFA1" in doc.xref_object(doc.pdf_catalog(), compressed=True) or \
                        "GTS_PDFA1" in "".join(doc.xref_object(int(r), compressed=True) for r in
                                               re.findall(r"(\d+)\s+0\s+R", doc.xref_get_key(doc.pdf_catalog(), "OutputIntents")[1]))
    except Exception as e:
        raise ValueError(f"o arquivo gerado não pôde ser reaberto para conferência ({e})")
    if not (ok_pages and ok_meta and ok_intent):
        raise ValueError("o arquivo gerado não passou na conferência final "
                         f"(páginas {'ok' if ok_pages else 'diferentes'}, identificação PDF/A "
                         f"{'ok' if ok_meta else 'ausente'}, perfil de cor {'ok' if ok_intent else 'ausente'})")

def _cert_name(name):
    """Nome legível de um certificado (o CN, que no ICP-Brasil costuma ser 'NOME:CPF')."""
    native = name.native
    return native.get("common_name") or native.get("organization_name") or name.human_friendly

def _format_dt(dt):
    return dt.astimezone().strftime("%d/%m/%Y %H:%M") if dt else "—"

MODIFICATION_TEXT = {
    "NONE": ("ok", "Nenhuma alteração"),
    "LTA_UPDATES": ("ok", "Só acréscimos técnicos (carimbos de tempo, dados de validação ou metadados)"),
    "FORM_FILLING": ("ok", "Só outras assinaturas ou preenchimento de formulário"),
    "ANNOTATIONS": ("warn", "Comentários ou anotações foram adicionados"),
    "OTHER": ("warn", "Houve outros acréscimos (por exemplo, uma conversão para PDF/A). "
                      "O conteúdo assinado continua intacto"),
}

CERTIFICATION_LEVELS = {1: "não permitia nenhuma alteração", 2: "só permitia preencher formulários e assinar",
                        3: "só permitia preencher formulários, assinar e comentar"}

def _describe_signature(embedded, status, index, count, later_fields):
    """Transforma o resultado da verificação em linhas para exibir: (rótulo, texto, tom).
    Só confere a integridade; quem assinou é exibido como consta no certificado, sem confirmação."""
    sig = embedded.sig_object
    cert = status.signing_cert
    certification = embedded.docmdp_level is not None
    reason = None

    issuer = _cert_name(cert.issuer)
    if cert.subject == cert.issuer:
        issuer += " (o próprio assinante: certificado autoassinado)"
    rows = [("Assinado por", _cert_name(cert.subject), None),
            ("Emitido por", issuer, None)]
    if status.timestamp_validity is not None:
        rows.append(("Data", f"{_format_dt(status.timestamp_validity.timestamp)} (carimbo de tempo)", None))
    else:
        rows.append(("Data", f"{_format_dt(status.signer_reported_dt)} (informada pelo assinante)", None))
    rows.append(("Validade do certificado", f"{_format_dt(cert.not_valid_before)} a {_format_dt(cert.not_valid_after)}", None))
    for key, label in (("/Reason", "Motivo"), ("/Location", "Local")):
        if sig.get(key):
            rows.append((label, str(sig[key]), None))
    level_no = int(embedded.docmdp_level.value) if certification else None
    if certification:
        rows.append(("Tipo", f"Certificação do documento (nível {level_no}): "
                             f"{CERTIFICATION_LEVELS.get(level_no, 'limitava as alterações')} depois dela", None))

    intact = status.intact and status.valid
    if intact:
        rows.append(("Integridade", "O conteúdo assinado não foi alterado", "ok"))
    elif not status.intact:
        rows.append(("Integridade", "O conteúdo foi alterado depois de assinado", "bad"))
        reason = ("O conteúdo coberto por esta assinatura não é mais o mesmo que foi assinado: "
                  "o documento foi modificado depois.")
    else:
        rows.append(("Integridade", "A assinatura não confere com o certificado", "bad"))
        reason = "Os dados criptográficos da assinatura não conferem com o certificado que ela traz."

    tone = None
    if status.intact:
        modification = getattr(status.modification_level, "name", "OTHER")
        tone, text = MODIFICATION_TEXT.get(modification, MODIFICATION_TEXT["OTHER"])
        after = (f"vieram {len(later_fields)} outra(s) assinatura(s) ({', '.join(later_fields)})"
                 if later_fields else "foram feitos outros acréscimos")
        if certification and not status.docmdp_ok:
            # critério rigoroso: incluir um campo de assinatura visível já conta como alteração fora do nível 2
            tone, text = "warn", f"Depois desta certificação {after}"
            reason = (f"Esta é uma assinatura de certificação, que "
                      f"{CERTIFICATION_LEVELS.get(level_no, 'limitava as alterações')}. Depois dela {after}, e "
                      "pelo critério mais rigoroso isso vai além do permitido. O caso mais comum é a "
                      "inclusão de um carimbo de assinatura visível na página, que muitos sistemas fazem e "
                      "o Adobe Reader aceita. O conteúdo certificado continua intacto.")
        elif tone == "warn":
            reason = f"{text}."
        rows.append(("Depois da assinatura", text, tone))

    if not intact:
        verdict, headline = "bad", "QUEBRADA"
    elif tone == "warn":
        verdict, headline = "warn", "ÍNTEGRA, COM RESSALVAS"
    else:
        verdict, headline = "ok", "ÍNTEGRA"
    return {"verdict": verdict, "headline": headline, "reason": reason,
            "title": f"Assinatura {index} de {count}" + (f" · campo {embedded.field_name}" if embedded.field_name else ""),
            "rows": rows}

def verify_signatures(input_files, output_dir=None, progress_callback=None, cancel_event=None):
    """Lista as assinaturas digitais dos PDFs e confere se o conteúdo assinado continua intacto.
    Não confirma a identidade de quem assinou: isso cabe a um validador oficial."""
    import logging
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
    logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)

    results = []
    errors = []
    counts = {"ok": 0, "warn": 0, "bad": 0}
    total = len(input_files)
    for i, pdf_path in enumerate(input_files):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Verificando {pdf.name}...")

        entry = {"file": pdf.name, "signatures": [], "error": None}
        try:
            with open(pdf, "rb") as fh:
                reader = PdfFileReader(fh, strict=False)
                if reader.encrypted:
                    raise ValueError("o arquivo está protegido por senha")
                embedded = reader.embedded_signatures
                for n, sig in enumerate(embedded, start=1):
                    _check_cancel(cancel_event)
                    # sem lista de autoridades e sem internet: a cadeia não é avaliada, só a integridade
                    context = ValidationContext(trust_roots=[], allow_fetching=False)
                    try:
                        status = validate_pdf_signature(sig, context)
                        later = [s.field_name or f"assinatura {k}" for k, s in enumerate(embedded[n:], start=n + 1)]
                        info = _describe_signature(sig, status, n, len(embedded), later)
                    except Exception as e:
                        info = {"verdict": "bad", "headline": "NÃO VERIFICADA",
                                "reason": "O programa não conseguiu ler esta assinatura.",
                                "title": f"Assinatura {n} de {len(embedded)}",
                                "rows": [("Erro", f"Não foi possível ler esta assinatura: {e}", "bad")]}
                    counts[info["verdict"]] += 1
                    entry["signatures"].append(info)
        except Exception as e:
            entry["error"] = str(e)
            errors.append(f"{pdf.name}: {e}")
        results.append(entry)

    if progress_callback:
        found = sum(counts.values())
        if found:
            labels = {"ok": "íntegra(s)", "warn": "com ressalvas", "bad": "quebrada(s)"}
            parts = [f"{counts[k]} {label}" for k, label in labels.items() if counts[k]]
            progress_callback(total, total, f"Concluído! {found} assinatura(s): " + ", ".join(parts))
        else:
            progress_callback(total, total, "Concluído! Nenhuma assinatura digital encontrada.")
    return {"errors": errors, "results": results}
