import io
import shutil
from pathlib import Path
from pypdf import PdfWriter, PdfReader
from PIL import Image, ImageOps, ImageChops
import pymupdf

# Batch functions return a list of "arquivo: erro" strings instead of printing,
# because the packaged app has no console where print() output could be seen.
# They accept a threading.Event as cancel_event and raise Cancelled when it is set.

class Cancelled(BaseException):
    """Raised when the user cancels. Derives from BaseException so the per-file
    `except Exception` handlers don't swallow it."""

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
    """Stems used to name outputs. Inputs sharing a name (e.g. from different folders)
    get _2, _3... so their outputs don't overwrite each other."""
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

def _pages_to_bytes(reader, page_numbers):
    writer = PdfWriter()
    for p in page_numbers:
        writer.add_page(reader.pages[p])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()

def merge_pdfs(input_files, output_file, progress_callback=None, cancel_event=None):
    writer = PdfWriter()
    errors = []
    total = len(input_files)
    for i, pdf in enumerate(input_files):
        _check_cancel(cancel_event)
        if progress_callback:
            progress_callback(i, total, f"Adicionando {Path(pdf).name}...")
        try:
            writer.append(str(pdf))
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
            if num_pages < 2:
                errors.append(f"{pdf.name}: possui apenas {num_pages} página(s), não há o que dividir")
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
                    # A part always takes at least one page, even if that page alone exceeds the target.
                    # Find the largest end that fits: grow the step exponentially, then binary search,
                    # so each part costs O(log n) test writes instead of one write per page.
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
                out_file = Path(output_dir) / f"{stem}_parte{part}.pdf"
                out_file.write_bytes(_pages_to_bytes(reader, page_range))

        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def compress_pdfs(input_files, output_dir, compression_level="Média", progress_callback=None, cancel_event=None):
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
            # clone_from keeps metadata, bookmarks and links; compress_content_streams
            # only works on pages that already belong to a PdfWriter.
            writer = PdfWriter(clone_from=str(pdf))

            for page in writer.pages:
                _check_cancel(cancel_event)
                page.compress_content_streams()
                for img in page.images:
                    if len(img.data) <= 100_000:
                        continue
                    try:
                        obj = img.indirect_reference.get_object()
                        pil = img.image
                        # JPEG has no alpha channel: replacing a masked image would turn
                        # its transparent areas black.
                        if "/SMask" in obj or "/Mask" in obj or pil.mode in ("RGBA", "LA", "PA"):
                            continue
                        if pil.mode not in ("RGB", "L"):
                            pil = pil.convert("RGB")
                        buf = io.BytesIO()
                        pil.save(buf, format="JPEG", quality=img_quality, optimize=True)
                        # Re-encoding an already compressed JPEG at higher quality makes it bigger
                        if len(buf.getvalue()) < len(img.data):
                            img.replace(pil, quality=img_quality, optimize=True)
                    except Exception:
                        pass

            out_file = Path(output_dir) / f"{stem}_comprimido.pdf"
            buf = io.BytesIO()
            writer.write(buf)
            original_size = pdf.stat().st_size
            if len(buf.getvalue()) < original_size:
                out_file.write_bytes(buf.getvalue())
            else:
                # Nothing left to gain: never hand back a file bigger than the original
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
    errors = []
    total = len(input_files)
    # Pages are added one image at a time, so only one decoded image is in memory at once
    # (a batch of phone photos would otherwise need several GB of RAM).
    with pymupdf.open() as out_doc:
        for i, img_path in enumerate(input_files):
            _check_cancel(cancel_event)
            if progress_callback:
                progress_callback(i, total, f"Processando imagem {i+1} de {total}...")
            try:
                with Image.open(img_path) as src:
                    # Phone photos store orientation in EXIF; apply it or pages come out rotated.
                    # exif_transpose returns a loaded copy, so the file handle can be closed.
                    img = ImageOps.exif_transpose(src)
                if img.mode in ("RGBA", "P", "LA", "PA"):
                    img = img.convert("RGBA")
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1])
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                buf = io.BytesIO()
                img.save(buf, format="JPEG")
                # 150 dpi: same page size the previous Pillow-based export produced
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

MARGINS_MAX_PAGES = 7

def add_margins(input_files, output_dir, margin_x_mm=15, margin_y_mm=5, progress_callback=None, cancel_event=None):
    MM_TO_PT = 2.83465
    A4_W, A4_H = 595.0, 842.0
    mx, my = margin_x_mm * MM_TO_PT, margin_y_mm * MM_TO_PT
    safe_rect = pymupdf.Rect(mx, my, A4_W - mx, A4_H - my)

    errors = []
    total = len(input_files)
    for i, (pdf_path, stem) in enumerate(zip(input_files, _output_stems(input_files))):
        _check_cancel(cancel_event)
        pdf = Path(pdf_path)
        if progress_callback:
            progress_callback(i, total, f"Adicionando margens em {pdf.name}...")

        try:
            with pymupdf.open(str(pdf)) as doc, pymupdf.open() as out_doc:
                limit = min(doc.page_count, MARGINS_MAX_PAGES)
                for p_num in range(limit):
                    _check_cancel(cancel_event)
                    page = doc[p_num]
                    out_page = out_doc.new_page(width=A4_W, height=A4_H)

                    if _is_page_colored(page):
                        # Transformação para Escala de Cinza
                        pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
                        img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=75, optimize=True)
                        out_page.insert_image(safe_rect, stream=buf.getvalue())
                    else:
                        out_page.show_pdf_page(safe_rect, doc, page.number)

                out_file = Path(output_dir) / f"{stem}.pdf"
                out_doc.save(str(out_file), garbage=4, deflate=True)
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors

def rotate_pdf_visual(input_file, output_dir, rotations_dict):
    """Returns the path of the file written."""
    pdf = Path(input_file)
    # clone_from keeps bookmarks and links, which add_page() would drop
    writer = PdfWriter(clone_from=str(pdf))
    for i, page in enumerate(writer.pages):
        angle = rotations_dict.get(i, 0)
        if angle != 0:
            page.rotate(angle)

    out_file = Path(output_dir) / f"{pdf.stem}_rotacionado.pdf"
    with open(out_file, "wb") as f:
        writer.write(f)
    return out_file

def crop_pdf_visual(input_file, output_dir, crop_boxes_dict):
    """crop_boxes_dict maps page index to (x0, y0, x1, y1) relative (0..1) to the page as displayed.
    Returns the path of the file written."""
    pdf = Path(input_file)
    with pymupdf.open(str(pdf)) as doc:
        for i, box in crop_boxes_dict.items():
            if i < doc.page_count:
                page = doc[i]
                rel_x0, rel_y0, rel_x1, rel_y1 = box

                w = page.rect.width
                h = page.rect.height

                # Coordinates are relative to what the user saw (rotated, already-cropped page);
                # set_cropbox expects unrotated coordinates relative to the mediabox.
                rect = pymupdf.Rect(rel_x0 * w, rel_y0 * h, rel_x1 * w, rel_y1 * h) * page.derotation_matrix
                rect = rect + (page.cropbox.x0, page.cropbox.y0, page.cropbox.x0, page.cropbox.y0)
                page.set_cropbox(rect & page.mediabox)

        out_file = Path(output_dir) / f"{pdf.stem}_cortado.pdf"
        doc.save(str(out_file), garbage=4, deflate=True)
    return out_file

def pdf_to_images(input_files, output_dir, progress_callback=None, cancel_event=None):
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
                    out_file = Path(output_dir) / f"{stem}_pag_{page_num+1}.png"
                    pix.save(str(out_file))
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")

    if progress_callback:
        progress_callback(total, total, "Concluído!")
    return errors
