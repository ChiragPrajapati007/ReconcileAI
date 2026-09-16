"""
Document ingestion service.

Responsibilities:
  - Accept uploaded files (PDF, PNG, JPEG, TIFF, WebP)
  - Validate file type and size
  - Compute SHA-256 hash for duplicate detection
  - Save to local filesystem
  - Convert PDF pages to images for multimodal processing
  - Return metadata for downstream stages
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# Supported MIME types → file extensions
SUPPORTED_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
}

# Maximum upload size in bytes
MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@dataclass
class IngestedDocument:
    """Result of document ingestion."""
    storage_path: str
    filename: str                   # UUID-based safe name on disk
    original_filename: str
    mime_type: str
    file_size: int
    document_hash: str              # SHA-256 hex digest
    page_images: list[bytes] = field(default_factory=list)
    page_image_mime: str = "image/png"
    page_count: int = 1


class IngestionError(Exception):
    """Raised when document ingestion fails."""
    pass


def validate_file(
    content: bytes,
    filename: str,
    content_type: str | None,
) -> str:
    """Validate file type and size. Returns the resolved MIME type.

    Raises IngestionError on invalid input.
    """
    # Size check
    if len(content) > MAX_UPLOAD_BYTES:
        raise IngestionError(
            f"File too large: {len(content)} bytes exceeds "
            f"limit of {MAX_UPLOAD_BYTES} bytes ({settings.MAX_UPLOAD_SIZE_MB} MB)."
        )

    if len(content) == 0:
        raise IngestionError("Empty file uploaded.")

    # Determine MIME type from content_type header or file extension
    mime_type = _resolve_mime_type(content, filename, content_type)
    if mime_type not in SUPPORTED_TYPES:
        raise IngestionError(
            f"Unsupported file type: '{mime_type}'. "
            f"Supported types: {', '.join(SUPPORTED_TYPES.keys())}"
        )

    return mime_type


def _resolve_mime_type(
    content: bytes,
    filename: str,
    content_type: str | None,
) -> str:
    """Resolve MIME type from content-type header, magic bytes, or extension."""
    # Trust content_type if it's in our supported set
    if content_type and content_type in SUPPORTED_TYPES:
        return content_type

    # Check magic bytes
    if content[:4] == b"%PDF":
        return "application/pdf"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:2] == b"\xff\xd8":
        return "image/jpeg"
    if content[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"

    # Fall back to extension
    ext = Path(filename).suffix.lower()
    ext_map = {v: k for k, v in SUPPORTED_TYPES.items()}
    ext_map[".jpeg"] = "image/jpeg"
    if ext in ext_map:
        return ext_map[ext]

    return content_type or "application/octet-stream"


def save_document(
    content: bytes,
    original_filename: str,
    mime_type: str,
) -> IngestedDocument:
    """Save uploaded document to the local filesystem.

    Returns an IngestedDocument with metadata.
    """
    # Compute hash
    document_hash = hashlib.sha256(content).hexdigest()

    # Generate safe filename
    ext = SUPPORTED_TYPES.get(mime_type, ".bin")
    safe_name = f"{uuid.uuid4().hex}{ext}"

    # Ensure upload directory exists
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    storage_path = str(upload_dir / safe_name)
    with open(storage_path, "wb") as f:
        f.write(content)

    return IngestedDocument(
        storage_path=storage_path,
        filename=safe_name,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size=len(content),
        document_hash=document_hash,
    )


def prepare_pages(doc: IngestedDocument, content: bytes) -> IngestedDocument:
    """Convert document to page images for multimodal processing.

    - PDFs are converted to PNG images using pdf2image (poppler).
    - Images are returned as-is (single page).
    - If PDF conversion fails (poppler unavailable), falls back gracefully.
    """
    if doc.mime_type == "application/pdf":
        doc = _pdf_to_images(doc, content)
    else:
        # Image file → single page
        doc.page_images = [content]
        doc.page_image_mime = doc.mime_type
        doc.page_count = 1

    return doc


def _pdf_to_images(doc: IngestedDocument, content: bytes) -> IngestedDocument:
    """Convert PDF bytes to a list of PNG page images."""
    try:
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(content, dpi=200, fmt="png")
        page_bytes_list: list[bytes] = []
        for img in images:
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page_bytes_list.append(buf.getvalue())

        doc.page_images = page_bytes_list
        doc.page_image_mime = "image/png"
        doc.page_count = len(page_bytes_list)
        logger.info("Converted PDF to %d page images", doc.page_count)

    except Exception as exc:
        # Graceful degradation: if poppler is unavailable, send the raw PDF
        # bytes. Gemini can handle PDF directly.
        logger.warning(
            "PDF → image conversion failed (%s). Sending raw PDF to provider.",
            exc,
        )
        doc.page_images = [content]
        doc.page_image_mime = "application/pdf"
        doc.page_count = 1

    return doc
