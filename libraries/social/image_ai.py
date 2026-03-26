"""AI-powered image analysis via OpenRouter for social posts and library enrichment."""

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

        model = getattr(settings, "OPENROUTER_MODEL", "openai/gpt-5.4-mini")
        prompt = (
            f"You are analyzing a photo of a community book exchange box. "
            f"Context: the library is named '{library.name}' in {library.city}, "
            f"{library.country}.\n\n"
            f"Return a JSON object with exactly two keys:\n"
            f'- "alt_text": a concise image description for screen readers, '
            f"about 120-150 characters. Describe what is visible in the photo.\n"
            f'- "hashtags": a list of 8-12 relevant lowercase hashtags based on '
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
            max_tokens=400,
            timeout=REQUEST_TIMEOUT,
        )

        return _parse_response(response.choices[0].message.content)

    except Exception:
        logger.exception("AI image analysis failed for %s", image_path)
        return None


def enrich_library_from_image(image_path: Path, library) -> dict | None:
    """Analyze a library photo to generate a name and description.
    Returns {"name": str, "description": str} or None on failure."""
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

        model = getattr(settings, "OPENROUTER_MODEL", "openai/gpt-5.4-mini")
        prompt = (
            f"You are analyzing a photo of a community book exchange box. "
            f"Context: located at {library.address}, {library.city}, "
            f"{library.country}.\n\n"
            f"Return a JSON object with exactly two keys:\n"
            f'- "name": a short, descriptive title for this library (max 100 '
            f"characters). Capture its most distinctive feature.\n"
            f'- "description": a 1-3 sentence description (max 500 characters) '
            f"of the library's appearance, setting, and notable features. "
            f"This will also be used as image alt text for accessibility.\n\n"
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
            max_tokens=400,
            timeout=REQUEST_TIMEOUT,
        )

        return _parse_enrichment_response(response.choices[0].message.content)

    except Exception:
        logger.exception("AI library enrichment failed for %s", image_path)
        return None


def _parse_enrichment_response(content: str) -> dict | None:
    """Parse an AI enrichment response into name and description.
    Validates expected keys and truncates to model field limits."""
    try:
        text = _strip_code_fences(content)
        data = json.loads(text)

        name = data.get("name", "")
        description = data.get("description", "")

        if not isinstance(name, str) or not isinstance(description, str):
            logger.warning("AI enrichment response has unexpected types: %s", data)
            return None

        return {
            "name": name.strip()[:255],
            "description": description.strip()[:2000],
        }

    except (json.JSONDecodeError, KeyError, TypeError):
        logger.exception("Failed to parse AI enrichment response: %s", content)
        return None


def _strip_code_fences(content: str) -> str:
    """Remove markdown code fences from AI response text.
    Handles ```json ... ``` wrapping commonly returned by LLMs."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


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
        text = _strip_code_fences(content)
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
