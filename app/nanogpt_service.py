from typing import Dict, List, Optional

import requests

from config import NANOGPT_API_KEY, NANOGPT_BASE_URL, NANOGPT_MODEL


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
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 150,
    ) -> str:
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=120,
        )
        response.raise_for_status()

        payload = response.json()
        try:
            return payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exception:
            raise ValueError("NanoGPT returned an invalid chat completion.") from exception
