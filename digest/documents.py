"""Turn attachments and downloaded files into something the model can read.

Text PDFs become text. Scanned PDFs and images are kept as image files, which
Claude reads visually.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

MAX_TEXT_CHARS = 30_000
MAX_PDF_IMAGE_PAGES = 4
MIN_TEXT_CHARS = 200  # below this, a PDF is treated as scanned
SPARSE_CHARS_PER_PAGE = 600  # a picture-heavy PDF with less text than this per page is also viewed
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}


@dataclass
class Converted:
    text: str = ""
    image_paths: list[Path] = field(default_factory=list)
    readable: bool = True
    note: str = ""


def convert_pdf(data: bytes, out_dir: Path, stem: str) -> Converted:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # corrupt or encrypted
        return Converted(readable=False, note=f"PDF could not be opened ({type(exc).__name__})")
    text = "\n\n".join(page.get_text() for page in doc).strip()
    has_pictures = any(page.get_images() for page in doc)
    sparse = len(text) < MIN_TEXT_CHARS or (
        has_pictures and len(text) / max(doc.page_count, 1) < SPARSE_CHARS_PER_PAGE
    )
    if not sparse:
        return Converted(text=text[:MAX_TEXT_CHARS])
    # Scanned PDF, or a designed flyer whose details are partly in pictures:
    # render the first pages so Claude can look at them as well as read the text.
    images = []
    for i, page in enumerate(doc):
        if i >= MAX_PDF_IMAGE_PAGES:
            break
        path = out_dir / f"{stem}-p{i + 1}.png"
        page.get_pixmap(dpi=110).save(path)
        images.append(path)
    note = "scanned PDF, read as images" if len(text) < MIN_TEXT_CHARS else "flyer with pictures, read as text and images"
    if doc.page_count > MAX_PDF_IMAGE_PAGES:
        note += f" (first {MAX_PDF_IMAGE_PAGES} of {doc.page_count} pages)"
    return Converted(text=text, image_paths=images, note=note)


def convert_image(data: bytes, mime: str, out_dir: Path, stem: str) -> Converted:
    ext = IMAGE_TYPES.get(mime)
    if not ext:
        return Converted(readable=False, note=f"unsupported image type {mime}")
    path = out_dir / f"{stem}{ext}"
    path.write_bytes(data)
    return Converted(image_paths=[path])


def convert(data: bytes, mime: str, filename: str, out_dir: Path, stem: str) -> Converted:
    mime = (mime or "").lower()
    name = (filename or "").lower()
    if mime == "application/pdf" or name.endswith(".pdf"):
        return convert_pdf(data, out_dir, stem)
    if mime in IMAGE_TYPES:
        return convert_image(data, mime, out_dir, stem)
    if mime.startswith("text/"):
        return Converted(text=data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS])
    return Converted(readable=False, note=f"unsupported file type ({mime or 'unknown'})")
