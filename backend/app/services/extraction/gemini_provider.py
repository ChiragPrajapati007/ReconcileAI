"""
Google Gemini multimodal extraction provider.

Uses the official google-genai SDK with Gemini 2.0 Flash.
Sends document page images and a structured extraction prompt.
Returns raw extraction result with string monetary values.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from app.core.config import settings
from app.services.extraction.provider_base import ExtractionProvider
from app.services.extraction.schemas import RawExtractionResult, RawLineItem

logger = logging.getLogger(__name__)

# ── Extraction prompt ─────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """\
You are an expert financial document extraction system.

Analyze the provided invoice/receipt document image(s) and extract ALL financial data.

Return a JSON object with EXACTLY these fields:

{
  "vendor_name": "string or null",
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD string or null",
  "due_date": "YYYY-MM-DD string or null",
  "currency": "3-letter ISO code or null",
  "po_number": "PO/reference number or null",
  "subtotal": "numeric string or null",
  "tax_amount": "numeric string or null",
  "discount_amount": "numeric string or null",
  "grand_total": "numeric string or null",
  "line_items": [
    {
      "description": "string",
      "quantity": "numeric string",
      "unit_price": "numeric string",
      "line_total": "numeric string",
      "tax_rate_percent": "numeric string or null",
      "discount": "numeric string or null",
      "page_number": 1,
      "source_text": "original text from document for this line"
    }
  ],
  "extraction_notes": "any notes about document quality, handwriting, ambiguity"
}

Rules:
- Extract ONLY values that are clearly visible in the document.
- Do NOT invent, calculate, or hallucinate values that are absent.
- Use null for any field that is not visible or unclear.
- Monetary values must be plain numeric strings without currency symbols (e.g. "10000.00" not "₹10,000.00").
- Remove commas and currency symbols from numbers.
- Dates must be in YYYY-MM-DD format when possible.
- For handwritten documents, note any uncertainty in extraction_notes.
- page_number is 1-indexed.
- source_text should be the original text from the document for traceability.
"""

# ── JSON schema for structured output ─────────────────────────────────────────

_LINE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "description": {"type": "STRING", "nullable": True},
        "quantity": {"type": "STRING", "nullable": True},
        "unit_price": {"type": "STRING", "nullable": True},
        "line_total": {"type": "STRING", "nullable": True},
        "tax_rate_percent": {"type": "STRING", "nullable": True},
        "discount": {"type": "STRING", "nullable": True},
        "page_number": {"type": "INTEGER", "nullable": True},
        "source_text": {"type": "STRING", "nullable": True},
    },
}

_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "vendor_name": {"type": "STRING", "nullable": True},
        "invoice_number": {"type": "STRING", "nullable": True},
        "invoice_date": {"type": "STRING", "nullable": True},
        "due_date": {"type": "STRING", "nullable": True},
        "currency": {"type": "STRING", "nullable": True},
        "po_number": {"type": "STRING", "nullable": True},
        "subtotal": {"type": "STRING", "nullable": True},
        "tax_amount": {"type": "STRING", "nullable": True},
        "discount_amount": {"type": "STRING", "nullable": True},
        "grand_total": {"type": "STRING", "nullable": True},
        "line_items": {"type": "ARRAY", "items": _LINE_ITEM_SCHEMA},
        "extraction_notes": {"type": "STRING", "nullable": True},
    },
    "required": [
        "vendor_name",
        "invoice_number",
        "invoice_date",
        "grand_total",
        "line_items",
    ],
}


class GeminiProvider(ExtractionProvider):
    """Concrete extraction provider using Google Gemini 2.0 Flash."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.0-flash",
    ) -> None:
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._model = model
        if not self._api_key:
            raise ValueError(
                "GEMINI_API_KEY is required for GeminiProvider. "
                "Set it in .env or pass api_key explicitly."
            )
        self._client = genai.Client(api_key=self._api_key)

    @property
    def provider_name(self) -> str:
        return "google"

    @property
    def model_name(self) -> str:
        return self._model

    async def extract(
        self,
        document_pages: list[bytes],
        mime_type: str,
    ) -> RawExtractionResult:
        """Send page images to Gemini and extract structured invoice data."""
        # Build the content parts: images + prompt
        parts: list[genai_types.Part] = []
        for page_bytes in document_pages:
            parts.append(
                genai_types.Part.from_bytes(data=page_bytes, mime_type=mime_type)
            )
        parts.append(genai_types.Part.from_text(text=EXTRACTION_PROMPT))

        # Call Gemini with structured output
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=genai_types.Content(parts=parts, role="user"),
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_EXTRACTION_SCHEMA,
                    temperature=0.1,  # Low temperature for factual extraction
                ),
            )
        except Exception as exc:
            logger.error("Gemini extraction failed: %s", exc)
            raise

        # Parse the structured JSON response
        raw_text = response.text
        if not raw_text:
            logger.warning("Gemini returned empty response")
            return RawExtractionResult()

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Gemini returned invalid JSON: %s", raw_text[:500])
            return RawExtractionResult(extraction_notes="Model returned invalid JSON")

        return self._parse_response(data)

    def _parse_response(self, data: dict[str, Any]) -> RawExtractionResult:
        """Convert Gemini JSON response into RawExtractionResult."""
        line_items = []
        for item_data in data.get("line_items", []):
            line_items.append(
                RawLineItem(
                    description=item_data.get("description"),
                    quantity=item_data.get("quantity"),
                    unit_price=item_data.get("unit_price"),
                    line_total=item_data.get("line_total"),
                    tax_rate_percent=item_data.get("tax_rate_percent"),
                    discount=item_data.get("discount"),
                    page_number=item_data.get("page_number"),
                    source_text=item_data.get("source_text"),
                )
            )

        return RawExtractionResult(
            vendor_name=data.get("vendor_name"),
            invoice_number=data.get("invoice_number"),
            invoice_date=data.get("invoice_date"),
            due_date=data.get("due_date"),
            currency=data.get("currency"),
            po_number=data.get("po_number"),
            subtotal=data.get("subtotal"),
            tax_amount=data.get("tax_amount"),
            discount_amount=data.get("discount_amount"),
            grand_total=data.get("grand_total"),
            line_items=line_items,
            extraction_notes=data.get("extraction_notes"),
        )
