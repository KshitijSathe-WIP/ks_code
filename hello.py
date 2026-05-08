print("Hello, world!")

import json
import os
from pathlib import Path
import urllib.request
import urllib.error


def load_env_file(file_path: str = ".env") -> None:
    env_path = Path(file_path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()

endpoint = "https://td-bank.services.ai.azure.com/openai/v1"
deployment_name = "gpt-4.1"
api_key = os.getenv("AZURE_OPENAI_API_KEY")

if not api_key:
    raise RuntimeError("Missing environment variable: AZURE_OPENAI_API_KEY")

url = f"{endpoint}/chat/completions"
payload = {
    "model": deployment_name,
    "messages": [
        {
            "role": "user",
            "content": "What is the capital of India?",
        }
    ],
}

request = urllib.request.Request(
    url=url,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "api-key": api_key,
    },
    method="POST",
)

try:
    with urllib.request.urlopen(request) as response:
        result = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    details = exc.read().decode("utf-8", errors="replace")
    raise RuntimeError(f"API request failed ({exc.code}): {details}") from exc

print(result["choices"][0]["message"]["content"])