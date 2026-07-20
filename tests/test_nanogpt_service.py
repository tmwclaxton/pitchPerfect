import base64
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

    def test_chat_json_requests_structured_output(self):
        session = FakeSession(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"attractiveness": 8, "slimness": 7, "quirkiness": 5, "notes": "Bright"}'
                        }
                    }
                ]
            }
        )
        service = NanoGptService(api_key="test-key", session=session)

        result = service.chat_json([{"role": "user", "content": "Score this profile"}])

        self.assertEqual(8, result["attractiveness"])
        self.assertEqual({"type": "json_object"}, session.request["json"]["response_format"])

    def test_chat_with_images_embeds_base64_payload(self):
        image_path = Path(__file__).parent / "fixtures" / "tiny.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            )
        )

        session = FakeSession(
            {"choices": [{"message": {"content": '{"attractiveness": 6}'}}]}
        )
        service = NanoGptService(api_key="test-key", session=session)

        result = service.chat_with_images(
            prompt="Score this",
            image_paths=[str(image_path)],
            system_prompt="Return JSON",
            json_response=True,
        )

        self.assertEqual(6, result["attractiveness"])
        user_content = session.request["json"]["messages"][1]["content"]
        self.assertEqual("text", user_content[0]["type"])
        self.assertTrue(
            user_content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )


if __name__ == "__main__":
    unittest.main()
