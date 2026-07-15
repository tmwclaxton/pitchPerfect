import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from nanogpt_service import NanoGptService


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def post(self, url, **kwargs):
        self.request = {"url": url, **kwargs}
        return FakeResponse(self.payload)


class NanoGptServiceTest(unittest.TestCase):
    def test_chat_sends_an_openai_compatible_nanogpt_request(self):
        session = FakeSession(
            {"choices": [{"message": {"content": "  A friendly reply.  "}}]}
        )
        service = NanoGptService(
            api_key="test-key",
            base_url="https://nano-gpt.test/api/v1/",
            model="test-model",
            session=session,
        )

        result = service.chat(
            [{"role": "user", "content": "Hello"}],
            temperature=0.5,
            max_tokens=50,
        )

        self.assertEqual("A friendly reply.", result)
        self.assertEqual(
            "https://nano-gpt.test/api/v1/chat/completions",
            session.request["url"],
        )
        self.assertEqual(
            "Bearer test-key",
            session.request["headers"]["Authorization"],
        )
        self.assertEqual("test-model", session.request["json"]["model"])
        self.assertEqual(50, session.request["json"]["max_tokens"])
        self.assertEqual(0.5, session.request["json"]["temperature"])

    def test_chat_rejects_an_invalid_response(self):
        service = NanoGptService(
            api_key="test-key",
            session=FakeSession({"choices": []}),
        )

        with self.assertRaisesRegex(
            ValueError, "NanoGPT returned an invalid chat completion"
        ):
            service.chat([{"role": "user", "content": "Hello"}])

    def test_api_key_is_required(self):
        with self.assertRaisesRegex(ValueError, "NANOGPT_API_KEY is required"):
            NanoGptService(api_key="")


if __name__ == "__main__":
    unittest.main()
