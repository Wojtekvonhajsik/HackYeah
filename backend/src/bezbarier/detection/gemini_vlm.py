"""Detektor oparty o Gemini (domyślnie Flash-Lite - tani i szybki) z odpowiedzią w JSON wg schematu.

Klucz: zmienna GEMINI_API_KEY (https://aistudio.google.com/apikey). Model: GEMINI_MODEL.
"""

from __future__ import annotations

import base64
import os

from google import genai

from .base import Detection, ImageRef
from .vlm_common import SYSTEM_PROMPT, VlmResult, load_image_bytes, media_type, to_detections, user_prompt

DEFAULT_MODEL = "gemini-3.5-flash-lite"


class GeminiVisionDetector:
    def __init__(self, client: genai.Client | None = None, model: str | None = None):
        self.client = client or genai.Client()
        self.model_name = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    def detect(self, image: ImageRef, image_bytes: bytes | None = None) -> list[Detection]:
        data = load_image_bytes(image, image_bytes)  # Gemini nie pobiera obrazów z dowolnych URL-i
        interaction = self.client.interactions.create(
            model=self.model_name,
            system_instruction=SYSTEM_PROMPT,
            input=[
                {"type": "image", "data": base64.b64encode(data).decode("utf-8"), "mime_type": media_type(data)},
                {"type": "text", "text": user_prompt(image)},
            ],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": VlmResult.model_json_schema(),
            },
            store=False,  # nie przechowujemy zdjęć/odpowiedzi po stronie Google
        )
        if not interaction.output_text:
            return []
        return to_detections(VlmResult.model_validate_json(interaction.output_text))
