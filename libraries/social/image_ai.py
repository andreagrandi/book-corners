"""AI-powered image analysis for social media posts via OpenRouter."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
REQUEST_TIMEOUT = 15


def analyze_library_image(image_path: Path, library) -> dict | None:
    """Analyze a library photo with a vision model to generate alt text and hashtags.
    Returns {"alt_text": str, "hashtags": list[str]} or None on failure."""
    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    if not api_key:
        return None

    try:
        from openai import OpenAI

        image_data = _encode_image(image_path)
        if not image_data:
            return None

        client = OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
        )

        model = getattr(settings, "OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")
        prompt = (
            f"You are analyzing a photo of a little free library (book exchange box). "
            f"Context: the library is named '{library.name}' in {library.city}, "
            f"{library.country}.\n\n"
            f"Return a JSON object with exactly two keys:\n"
            f'- "alt_text": a concise image description for screen readers, '
            f"about 120-150 characters. Describe what is visible in the photo.\n"
            f'- "hashtags": a list of 3-5 relevant lowercase hashtags based on '
            f"what you see in the image (without the # prefix). Focus on visual "
            f"elements like the style, setting, or notable features.\n\n"
            f"Respond with only the JSON object, no other text."
        )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                        },
                    ],
                }
            ],
            max_tokens=300,
            timeout=REQUEST_TIMEOUT,
        )

        return _parse_response(response.choices[0].message.content)

    except Exception:
        logger.exception("AI image analysis failed for %s", image_path)
        return None


def _encode_image(image_path: Path) -> str | None:
    """Read and base64-encode an image file as JPEG.
    Returns the encoded string or None on failure."""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        logger.exception("Failed to read image file %s", image_path)
        return None


def _parse_response(content: str) -> dict | None:
    """Parse the AI model response into alt_text and hashtags.
    Returns None if the response is not valid JSON with expected keys."""
    try:
        # Strip markdown code fences if present
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)

        alt_text = data.get("alt_text", "")
        hashtags = data.get("hashtags", [])

        if not isinstance(alt_text, str) or not isinstance(hashtags, list):
            logger.warning("AI response has unexpected types: %s", data)
            return None

        # Clean hashtags: ensure lowercase, strip # prefix if present
        clean_hashtags = [
            tag.lstrip("#").lower()
            for tag in hashtags
            if isinstance(tag, str) and tag.strip()
        ]

        return {
            "alt_text": alt_text.strip(),
            "hashtags": clean_hashtags,
        }

    except (json.JSONDecodeError, KeyError, TypeError):
        logger.exception("Failed to parse AI response: %s", content)
        return None
