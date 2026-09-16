"""
Abstract base class for extraction providers.

Concrete implementations (Gemini, OpenAI, etc.) must subclass this.
The provider abstraction keeps the pipeline decoupled from any single model.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.extraction.schemas import RawExtractionResult


class ExtractionProvider(ABC):
    """Interface that every AI extraction provider must implement."""

    @abstractmethod
    async def extract(
        self,
        document_pages: list[bytes],
        mime_type: str,
    ) -> RawExtractionResult:
        """Extract structured invoice data from document page images.

        Args:
            document_pages: list of page images as raw bytes (PNG/JPEG).
            mime_type: MIME type of the page images (e.g. "image/png").

        Returns:
            RawExtractionResult with string monetary values.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier for the provider, e.g. 'google'."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier, e.g. 'gemini-2.0-flash'."""
        ...
