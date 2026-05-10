"""
LLM helpers for the newsletter generator.

Ollama  — article analysis (structured output) and newsletter writing (chat)
Claude CLI — weekly source curation only (saves tokens)
"""

import os
import subprocess

import ollama

from .config import OLLAMA_HOST, OLLAMA_MODEL

# Single client for the whole process lifetime
_client = ollama.Client(host=OLLAMA_HOST)


def warmup_ollama():
    """
    Preload OLLAMA_MODEL into memory before generation starts.
    Prevents cold-start timeouts on large models (70b takes ~60s to load).
    """
    print(f"  Loading model {OLLAMA_MODEL} into memory...")
    try:
        _client.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": "ready"}],
            options={"num_predict": 1},
        )
        print("  Model ready")
    except ollama.ResponseError as e:
        print(f"  ! Model warmup failed ({e}) — will try anyway")


def generate_with_ollama(prompt: str) -> str:
    """Generate content using local Ollama (free-form text)."""
    response = _client.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"num_predict": 512},
    )
    content = response.message.content.strip()
    if not content:
        raise RuntimeError("Ollama returned empty content")
    return content


def generate_with_claude_cli(prompt: str) -> str:
    """
    Generate content using the local `claude` CLI (uses current session auth).
    Unsets CLAUDECODE so --print mode works when called from inside Claude Code.
    """
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        ["claude", "--print", "--model", "claude-sonnet-4-6"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr.strip()}")
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("claude CLI returned empty output")
    return output


def _call_ollama_structured[T](messages: list[dict], schema: type[T]) -> T | None:
    """
    Ollama chat with JSON schema enforcement.

    Passing `format=schema` tells Ollama to constrain its output to valid JSON
    matching the Pydantic model's schema — the model cannot produce malformed
    responses. No text parsing, no fragile string matching on the caller side.
    """
    try:
        response = _client.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            format=schema.model_json_schema(),
            options={"num_predict": 1024, "temperature": 0.1},
        )
        return schema.model_validate_json(response.message.content)
    except (ollama.ResponseError, Exception) as e:
        print(f"  ! Structured output failed: {e}")
        return None


def _call_ollama_chat(messages: list[dict]) -> str | None:
    """Multi-turn Ollama chat. Returns the assistant content string."""
    try:
        response = _client.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            options={"num_predict": 1024, "temperature": 0.2},
        )
        return response.message.content.strip()
    except ollama.ResponseError as e:
        print(f"  ! Ollama chat failed: {e}")
        return None
