"""Detektor oparty o model wizyjny Claude ze structured outputs."""

from __future__ import annotations

import base64

import anthropic

from .base import Detection, ImageRef
from .vlm_common import SYSTEM_PROMPT, VlmResult, media_type, to_detections, user_prompt

MODEL = "claude-opus-5-5"


class ClaudeVisionDetector:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str = MODEL):
        self.client = client or anthropic.Anthropic()
        self.model_name = model

    def detect(self, image: ImageRef, image_bytes: bytes | None = None) -> list[Detection]:
        if image_bytes is not None:
            source = {
                "type": "base64",
                "media_type": media_type(image_bytes),
                "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
            }
        elif image.url:
            source = {"type": "url", "url": image.url}
        else:
            raise ValueError("Potrzebny image_bytes albo image.url")

        response = self.client.messages.parse(
            model=self.model_name,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": source},
                    {"type": "text", "text": user_prompt(image)},
                ],
            }],
            output_format=VlmResult,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return []
        return to_detections(response.parsed_output)
