"""AI-powered thread summarization using Claude."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_MODEL = "sonnet"
PROMPT_FILE = Path(__file__).parent.parent / "PROMPT.md"

# JSON Schema for structured output
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-4 sentence summary of the discussion"},
        "importance": {"type": "string", "enum": ["high", "medium", "low"]},
        "urgency": {"type": "string", "enum": ["high", "medium", "low"]},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "participants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "count": {"type": "integer"}
                },
                "required": ["name", "count"]
            }
        }
    },
    "required": ["summary", "importance", "urgency"]
}

DEFAULT_PROMPT = """Analyze this Zulip thread. Summarize and classify importance/urgency.

importance: high = directly affects user, blocks work, or requires action; medium = useful discussion; low = informational only
urgency: high = needs attention today; medium = this week; low = no time pressure

Thread:
"""


def get_prompt() -> str:
    """Load prompt from PROMPT.md or use default."""
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text()
    return DEFAULT_PROMPT


def format_messages(messages: List[Dict[str, Any]]) -> str:
    """Format messages for Claude input."""
    lines = []
    for msg in messages:
        ts = datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"[{ts}] {msg['sender_name']}:")
        # Prefer markdown content, fall back to stripped HTML for old messages
        content = msg.get("content_markdown") or msg["content_text"]
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


def extract_json(text: str) -> Optional[str]:
    """Extract JSON object from text that might have extra content."""
    # Find the first { and match braces
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i, c in enumerate(text[start:], start):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


MAX_MESSAGES = 200  # Limit to avoid prompt length issues


def generate_summary(
    messages: List[Dict[str, Any]], model: str = DEFAULT_MODEL
) -> Dict[str, Any]:
    """Generate summary using claude -p with JSON schema enforcement.

    Returns dict with: summary, importance, urgency, key_points, action_items, participants

    Note: Requires Claude Code CLI (`claude`) to be installed and available in PATH.
    Install from: https://github.com/anthropics/claude-code
    """
    prompt = get_prompt()

    # Truncate long threads to last MAX_MESSAGES
    total_messages = len(messages)
    if total_messages > MAX_MESSAGES:
        omitted = total_messages - MAX_MESSAGES
        messages = messages[-MAX_MESSAGES:]
        truncation_note = f"[Note: Thread has {total_messages} messages. Showing last {MAX_MESSAGES}; {omitted} earlier messages omitted.]\n\n"
    else:
        truncation_note = ""

    thread_text = format_messages(messages)
    full_input = f"{prompt}\n{truncation_note}{thread_text}"

    result = subprocess.run(
        [
            "claude", "-p",
            "--model", model,
            "--tools", "",  # Disable built-in tools
            "--mcp-config", "{}", "--strict-mcp-config",  # Disable all MCP servers
            "--output-format", "json",
            "--json-schema", json.dumps(SUMMARY_SCHEMA),
        ],
        input=full_input,  # Pass prompt via stdin to avoid arg length limits
        capture_output=True,
        text=True,
        timeout=300,  # 5 minute timeout for large threads
    )

    if result.returncode != 0:
        # Include both stderr and stdout for diagnosis
        error_detail = result.stderr.strip() if result.stderr.strip() else result.stdout[:500]
        raise RuntimeError(f"Claude failed (exit {result.returncode}): {error_detail}")

    response = result.stdout.strip()

    # Parse the wrapper JSON to get structured_output
    try:
        wrapper = json.loads(response)
        if wrapper.get("is_error"):
            raise RuntimeError(f"Claude error: {wrapper.get('result', 'unknown error')}")
        data = wrapper.get("structured_output")
        if not data:
            # Fall back to extracting JSON from result text
            result_text = wrapper.get("result", "")
            json_str = extract_json(result_text)
            if json_str:
                data = json.loads(json_str)
            else:
                raise RuntimeError(f"No structured_output in response: {response[:500]}")
    except json.JSONDecodeError:
        # Fall back to old behavior for non-JSON output
        json_str = extract_json(response)
        if not json_str:
            raise RuntimeError(f"Could not parse JSON from response: {response[:500]}")
        data = json.loads(json_str)

    # Validate required fields
    required = ["summary", "importance", "urgency"]
    for field in required:
        if field not in data:
            raise RuntimeError(f"Missing required field: {field}")

    # Validate enum values
    if data["importance"] not in ("high", "medium", "low"):
        data["importance"] = "medium"
    if data["urgency"] not in ("high", "medium", "low"):
        data["urgency"] = "low"

    # Ensure optional fields exist
    data.setdefault("key_points", [])
    data.setdefault("action_items", [])
    data.setdefault("participants", [])

    return data
