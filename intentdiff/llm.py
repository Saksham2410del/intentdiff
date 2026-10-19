"""Small wrapper around the Gemini API, with a disk cache.

The cache matters for evaluation: re-running the benchmark should cost
nothing and give exactly the same answers as the first run.
"""
import hashlib
import json
import os
import time
from pathlib import Path

from intentdiff import config

ATTEMPTS = 5  # one call plus up to four retries for temporary errors


class LLMError(Exception):
    pass


class GeminiClient:
    def __init__(self, model=config.MODEL, cache_dir=config.CACHE_DIR):
        self.model = model
        self.cache_dir = Path(cache_dir)
        self.calls = 0
        self.cache_hits = 0
        self.errors = 0          # calls that got no answer in the end
        self.input_tokens = 0
        self.output_tokens = 0
        self._client = None

    def _connect(self):
        if self._client is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise LLMError("GEMINI_API_KEY is not set")
            from google import genai  # imported here so the rest of the project runs without it
            self._client = genai.Client(api_key=api_key)
        return self._client

    def generate_json(self, prompt, schema, temperature=0.0):
        """Ask for JSON that matches the pydantic model `schema`. Returns the raw text."""
        key = hashlib.sha256(f"{self.model}|{temperature}|{prompt}".encode()).hexdigest()
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            self.cache_hits += 1
            return json.loads(cache_file.read_text(encoding="utf-8"))["text"]

        from google.genai import types
        settings = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
        )

        response = None
        for attempt in range(ATTEMPTS):
            try:
                response = self._connect().models.generate_content(
                    model=self.model, contents=prompt, config=settings
                )
                break
            except LLMError:
                self.errors += 1
                raise
            except Exception as e:  # network trouble, rate limits, bad key - all mean "no answer"
                # Per-minute rate limits (429) and "model overloaded" (503) usually clear up
                # within a minute, so wait and try again. A bad key, a bad request or a used-up
                # daily quota won't.
                code = getattr(e, "code", None)
                temporary = not isinstance(code, int) or code == 429 or code >= 500
                if "PerDay" in str(e) or not temporary or attempt == ATTEMPTS - 1:
                    self.errors += 1
                    raise LLMError(f"{type(e).__name__}: {e}") from e
                time.sleep(5 * 2 ** attempt)  # 5, 10, 20, 40 s

        text = response.text
        if not text:
            raise LLMError("the model returned an empty response")

        usage = response.usage_metadata
        input_tokens = (usage.prompt_token_count or 0) if usage else 0
        output_tokens = (usage.candidates_token_count or 0) if usage else 0
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "model": self.model,
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }), encoding="utf-8")
        return text
