# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()

NANOGPT_API_KEY = os.getenv("NANOGPT_API_KEY")
NANOGPT_BASE_URL = os.getenv("NANOGPT_BASE_URL", "https://nano-gpt.com/api/v1")
NANOGPT_MODEL = os.getenv("NANOGPT_MODEL", "openai/gpt-4.1-mini")
