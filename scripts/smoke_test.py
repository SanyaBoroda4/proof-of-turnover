"""Smoke test for the Meta Model API used by Proof of Turnover.

Checks:
  a) GET /v1/models lists models.
  b) muse-spark-1.3-contributor answers a short Responses API prompt.
  c) sam-3.1 segments 3 red squares in a generated test image.

Every API call retries up to 5 times with exponential backoff on
HTTP 404, 429 and 5xx. Prints an OK/FAIL line per check and exits
non-zero if any check fails.

Run from the project root after putting your key in .env:

    .venv\\Scripts\\python scripts\\smoke_test.py
"""

import base64
import os
import sys
import time

import cv2
import numpy
from dotenv import load_dotenv
from openai import APIStatusError, OpenAI

import meta_sam_parser

load_dotenv()

BASE_URL = "https://api.meta.ai/v1"
SPARK_MODEL = "muse-spark-1.3-contributor"
SAM_MODEL = "sam-3.1"

MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0


class _RetryableError(Exception):
    """Internal soft failure (e.g. an empty model list) worth retrying."""


def _is_retryable_status(status_code):
    return status_code == 404 or status_code == 429 or 500 <= status_code <= 599


def call_with_retry(label, func):
    """Call func(), retrying up to MAX_RETRIES times with exponential backoff.

    Retries on HTTP 404, 429 and 5xx, plus internal soft failures such as
    the API's occasional mistakenly empty model list.
    """
    last_error = None
    for attempt in range(1 + MAX_RETRIES):
        try:
            return func()
        except APIStatusError as exc:
            last_error = exc
            if not _is_retryable_status(exc.status_code):
                raise
            reason = f"HTTP {exc.status_code}"
        except _RetryableError as exc:
            last_error = exc
            reason = str(exc)
        if attempt == MAX_RETRIES:
            break
        delay = BACKOFF_BASE_SECONDS * (2**attempt)
        print(f"[{label}] attempt {attempt + 1} failed ({reason}); "
              f"retrying in {delay:.0f}s...")
        time.sleep(delay)
    raise last_error


def check_models(client):
    print("=== Check (a): GET /v1/models ===")

    def fetch_models():
        models = list(client.models.list().data)
        if not models:
            raise _RetryableError("empty model list")
        return models

    try:
        models = call_with_retry("models", fetch_models)
    except Exception as exc:
        print(f"FAIL (a): could not list models: {exc}")
        return False
    print("Model ids:")
    for model in models:
        print(f"  - {model.id}")
    print(f"OK (a): listed {len(models)} model(s)")
    return True


def check_spark(client):
    print("=== Check (b): muse-spark-1.3-contributor ===")
    try:
        response = call_with_retry(
            "spark",
            lambda: client.responses.create(
                model=SPARK_MODEL,
                input="Reply with exactly one word: hello",
                max_output_tokens=2000,
            ),
        )
    except Exception as exc:
        print(f"FAIL (b): spark call failed: {exc}")
        return False
    answer = response.output_text
    print("Answer:")
    print(answer)
    if not answer.strip():
        print("FAIL (b): empty answer")
        return False
    print("OK (b): got an answer")
    return True


def make_test_image():
    """White 640x480 image with 3 separate solid red squares."""
    image = numpy.full((480, 640, 3), 255, dtype=numpy.uint8)
    red = (0, 0, 255)  # BGR
    cv2.rectangle(image, (50, 50), (150, 150), red, thickness=-1)
    cv2.rectangle(image, (270, 200), (370, 300), red, thickness=-1)
    cv2.rectangle(image, (480, 100), (580, 200), red, thickness=-1)
    return image


def check_sam(client):
    print("=== Check (c): sam-3.1 segmentation ===")
    image = make_test_image()
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        print("FAIL (c): could not PNG-encode the test image")
        return False
    data_url = "data:image/png;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")
    try:
        response = call_with_retry(
            "sam",
            lambda: client.responses.create(
                model=SAM_MODEL,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "red square"},
                            {"type": "input_image", "image_url": data_url},
                        ],
                    }
                ],
                metadata={"include_confidence": "true"},
            ),
        )
    except Exception as exc:
        print(f"FAIL (c): sam call failed: {exc}")
        return False
    try:
        parser = meta_sam_parser.image_segmentation_format().create_parser()
        parser.push(response.output_text)
        finish = parser.finish(meta_sam_parser.CompletedOutcome())
    except Exception as exc:
        print(f"FAIL (c): could not parse sam output: {exc}")
        return False
    found = [r for r in finish.result.records if r.kind in ("mask", "box")]
    print(f"Found {len(found)} red square(s) (expected 3)")
    if len(found) != 3:
        print("FAIL (c): expected 3 squares")
        return False
    print("OK (c): found all 3 squares")
    return True


def main():
    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        print(
            "ERROR: MODEL_API_KEY is not set "
            "(copy .env.example to .env and add your key).",
            file=sys.stderr,
        )
        return 1
    # SDK-level retries off: call_with_retry owns the retry policy.
    client = OpenAI(base_url=BASE_URL, api_key=api_key, max_retries=0)
    results = [
        check_models(client),
        check_spark(client),
        check_sam(client),
    ]
    print("=== Summary ===")
    if all(results):
        print("ALL CHECKS PASSED")
        return 0
    print("SOME CHECKS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
