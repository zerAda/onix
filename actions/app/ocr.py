"""ocr — Extraction de texte LOCALE (onix-actions).

100 % local, aucun service cloud :
  * PDF texte    -> pdfplumber (sinon pypdf) : extraction directe, rapide ;
  * PDF scanné   -> pdf2image (poppler) + pytesseract ;
  * image        -> pytesseract directement.

Dégrade PROPREMENT : si un binaire (tesseract, poppler) ou une lib Python est
absent, on ne lève pas — on retourne un résultat partiel avec `extraction_mode`
explicite (« unavailable ») et la raison, pour que l'appelant choisisse l'option
LLM ou demande un texte déjà extrait.

Sortie : { metadata: {source_file, extraction_mode, pages}, text, fields, tables }
— compatible `audit_engine.extract_canonical_fields`.
"""
from __future__ import annotations

import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# Imports optionnels : on ne casse pas si une dépendance manque.
try:
    import pdfplumber  # type: ignore
    _HAS_PDFPLUMBER = True
except Exception:  # pragma: no cover
    _HAS_PDFPLUMBER = False

try:
    from pypdf import PdfReader  # type: ignore
    _HAS_PYPDF = True
except Exception:  # pragma: no cover
    _HAS_PYPDF = False

try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
    _HAS_TESSERACT_LIB = True
except Exception:  # pragma: no cover
    _HAS_TESSERACT_LIB = False

try:
    from pdf2image import convert_from_bytes  # type: ignore
    _HAS_PDF2IMAGE = True
except Exception:  # pragma: no cover
    _HAS_PDF2IMAGE = False

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp")
PDF_EXTS = (".pdf",)
ALLOWED_EXTS = PDF_EXTS + IMAGE_EXTS

# En-dessous de ce nombre de caractères, un PDF est considéré « scanné » -> OCR.
_MIN_TEXT_CHARS = 40


def _tesseract_available() -> bool:
    """Le binaire tesseract est-il réellement installé (au-delà de la lib) ?"""
    if not _HAS_TESSERACT_LIB:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_capabilities() -> Dict[str, bool]:
    return {
        "pdf_text": _HAS_PDFPLUMBER or _HAS_PYPDF,
        "pdf_scanned": _HAS_PDF2IMAGE and _tesseract_available(),
        "image": _tesseract_available(),
    }


def _kv_pairs_from_text(text: str) -> Dict[str, Dict[str, str]]:
    """Heuristique « libellé : valeur » par ligne, vers le format `fields` OCR.
    Permet à `extract_canonical_fields` d'aliaser des libellés FR arbitraires."""
    fields: Dict[str, Dict[str, str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([^:]{2,60}?)\s*[:\-]\s+(.+)$", line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key and val and key not in fields:
                fields[key] = {"value": val, "confidence": 1.0}
    return fields


def _extract_pdf_text(data: bytes) -> Tuple[str, int]:
    """Texte d'un PDF déjà textuel. Retourne (texte, nb_pages)."""
    if _HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = pdf.pages
                text = "\n".join((p.extract_text() or "") for p in pages)
                return text, len(pages)
        except Exception:
            pass
    if _HAS_PYPDF:
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
            return text, len(reader.pages)
        except Exception:
            pass
    return "", 0


def _ocr_pdf_scanned(data: bytes, max_pages: int = 10) -> Tuple[str, int]:
    if not (_HAS_PDF2IMAGE and _tesseract_available()):
        return "", 0
    try:
        images = convert_from_bytes(data, dpi=200)
    except Exception:
        return "", 0
    texts: List[str] = []
    for img in images[:max_pages]:
        try:
            texts.append(pytesseract.image_to_string(img, lang=os.environ.get("ONIX_OCR_LANG", "fra+eng")))
        except Exception:
            try:
                texts.append(pytesseract.image_to_string(img))
            except Exception:
                continue
    return "\n".join(texts), len(images)


def _ocr_image(data: bytes) -> str:
    if not _tesseract_available():
        return ""
    try:
        img = Image.open(io.BytesIO(data))
        try:
            return pytesseract.image_to_string(img, lang=os.environ.get("ONIX_OCR_LANG", "fra+eng"))
        except Exception:
            return pytesseract.image_to_string(img)
    except Exception:
        return ""


def extract(data: bytes, filename: str, max_pages: int = 10) -> Dict[str, Any]:
    """Extrait texte + champs heuristiques d'un PDF/image, 100 % local.

    Ne lève jamais sur absence de binaire OCR : `extraction_mode` vaut alors
    « unavailable » et `metadata.reason` explique la dégradation.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    mode = "unavailable"
    reason: Optional[str] = None
    text = ""
    pages = 0

    if ext in PDF_EXTS:
        text, pages = _extract_pdf_text(data)
        if len(text.strip()) >= _MIN_TEXT_CHARS:
            mode = "pdf_text"
        else:
            ocr_text, ocr_pages = _ocr_pdf_scanned(data, max_pages=max_pages)
            if ocr_text.strip():
                text, pages, mode = ocr_text, ocr_pages or pages, "pdf_ocr"
            elif text.strip():
                mode = "pdf_text"  # peu de texte mais OCR indispo : on garde le peu obtenu
            else:
                reason = (
                    "PDF scanné : OCR indisponible (tesseract/poppler absent). "
                    "Fournissez un texte déjà extrait ou activez l'assistance LLM."
                )
    elif ext in IMAGE_EXTS:
        text = _ocr_image(data)
        if text.strip():
            mode, pages = "image_ocr", 1
        else:
            reason = "Image : OCR indisponible (tesseract absent)."
    else:
        reason = f"Extension non supportée : {ext or '(aucune)'}"

    return {
        "metadata": {
            "source_file": os.path.basename(filename or "document"),
            "extraction_mode": mode,
            "pages": pages,
            **({"reason": reason} if reason else {}),
        },
        "text": text,
        "fields": _kv_pairs_from_text(text),
        "tables": [],
    }
