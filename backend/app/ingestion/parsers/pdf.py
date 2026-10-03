import pymupdf


class PdfExtractionError(ValueError):
    pass


def extract_pdf_pages(content: bytes) -> list[tuple[int, str]]:
    """Return one-based page numbers and embedded text; OCR is intentionally absent."""
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            return [
                (index + 1, page.get_text("text", sort=True).strip())
                for index, page in enumerate(pdf)
            ]
    except Exception as exc:
        raise PdfExtractionError("Could not extract text from PDF") from exc
