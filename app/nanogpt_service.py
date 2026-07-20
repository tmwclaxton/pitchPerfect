import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests

from config import NANOGPT_API_KEY, NANOGPT_BASE_URL, NANOGPT_MODEL

MessageContent = Union[str, List[Dict[str, Any]]]


class NanoGptService:
    def __init__(
        self,
        api_key: Optional[str] = NANOGPT_API_KEY,
        base_url: str = NANOGPT_BASE_URL,
        model: str = NANOGPT_MODEL,
        session: Optional[requests.Session] = None,
    ):
        if not api_key:
            raise ValueError("NANOGPT_API_KEY is required.")

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.session = session or requests.Session()

    def chat(
        self,
        messages: List[Dict[str, MessageContent]],
        temperature: float = 0.7,
        max_tokens: int = 150,
        model: Optional[str] = None,
    ) -> str:
        payload = self._post_chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
        try:
            return payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exception:
            raise ValueError("NanoGPT returned an invalid chat completion.") from exception

    def chat_json(
        self,
        messages: List[Dict[str, MessageContent]],
        temperature: float = 0.2,
        max_tokens: int = 300,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = self._post_chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            response_format={"type": "json_object"},
        )
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, AttributeError) as exception:
            raise ValueError("NanoGPT returned an invalid chat completion.") from exception

        decoded = self._decode_json_content(content)
        if decoded is None:
            raise ValueError("NanoGPT returned content that could not be decoded as JSON.")
        return decoded

    def chat_with_images(
        self,
        prompt: str,
        image_paths: List[str],
        system_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 300,
        model: Optional[str] = None,
        json_response: bool = False,
    ) -> Union[str, Dict[str, Any]]:
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_data_uri(image_path)},
                }
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        if json_response:
            return self.chat_json(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                model=model,
            )

        return self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )

    def _post_chat(
        self,
        messages: List[Dict[str, MessageContent]],
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format

        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    def _image_data_uri(
        self,
        image_path: str,
        *,
        max_side: int = 1024,
        jpeg_quality: int = 75,
    ) -> str:
        """
        Encode an image as a data URI, downscaling/compressing to avoid
        NanoGPT 413 Request Entity Too Large on multi-photo vision calls.
        """
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {image_path}")

        try:
            from io import BytesIO

            from PIL import Image

            with Image.open(path) as image:
                rgb = image.convert("RGB")
                width, height = rgb.size
                longest = max(width, height)
                if longest > max_side:
                    scale = max_side / float(longest)
                    rgb = rgb.resize(
                        (max(1, int(width * scale)), max(1, int(height * scale))),
                        Image.Resampling.LANCZOS
                        if hasattr(Image, "Resampling")
                        else Image.LANCZOS,
                    )
                buffer = BytesIO()
                rgb.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
        except Exception:
            # Fall back to raw file bytes if Pillow cannot process the image.
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"

    def _decode_json_content(self, content: str) -> Optional[Dict[str, Any]]:
        stripped = content.strip()
        if not stripped:
            return None

        try:
            decoded = json.loads(stripped)
            return decoded if isinstance(decoded, dict) else None
        except json.JSONDecodeError:
            pass

        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        if fenced:
            try:
                decoded = json.loads(fenced.group(1))
                return decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                return None

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        try:
            decoded = json.loads(stripped[start : end + 1])
            return decoded if isinstance(decoded, dict) else None
        except json.JSONDecodeError:
            return None
