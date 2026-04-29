# -*- coding: utf-8 -*-
"""
Duplicate message detection for token discipline.

Scans assistant messages for repeating patterns and logs them for review.
Patterns are normalized by stripping UUIDs, request IDs, timestamps, etc.
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

LOGS_DIR = Path(__file__).parent.parent / "logs"


def normalize_for_comparison(text: str) -> str:
    """Normalize text by stripping variable parts like IDs and timestamps."""
    if not text:
        return ""
    
    result = text
    # Strip UUIDs
    result = re.sub(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        '__UUID__', result, flags=re.IGNORECASE
    )
    # Strip request/message IDs (msg_123456, req_abc123, etc.)
    result = re.sub(
        r'\b(msg|req|id|session|request)_[a-zA-Z0-9]+',
        '__ID__', result, flags=re.IGNORECASE
    )
    # Strip generic alphanumeric IDs after common prefixes
    result = re.sub(r'(ID|Id|id):\s*[a-zA-Z0-9_-]+', 'ID: __ID__', result)
    # Strip timestamps (ISO format)
    result = re.sub(
        r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?',
        '__TIMESTAMP__', result
    )
    # Strip unix timestamps
    result = re.sub(r'\b\d{10,13}\b', '__UNIX_TS__', result)
    # Strip line numbers in stack traces
    result = re.sub(r':\d+:\d+', ':__LINE__', result)
    # Strip hex-like IDs (3+ segments separated by dashes)
    result = re.sub(
        r'\b[a-zA-Z0-9]{2,}-[a-zA-Z0-9]{2,}-[a-zA-Z0-9]{2,}(-[a-zA-Z0-9]{2,})*',
        '__HEX_ID__', result
    )
    # Normalize whitespace
    result = re.sub(r'\s+', ' ', result).strip()
    
    return result


def hash_pattern(text: str) -> str:
    """Generate a short hash for a normalized pattern."""
    return hashlib.md5(text.encode()).hexdigest()[:12]


def extract_text_content(content: Any) -> str:
    """Extract text from various content formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
            elif hasattr(item, "type") and item.type == "text":
                texts.append(getattr(item, "text", ""))
        return " ".join(texts)
    return ""


def detect_and_log_duplicates(
    messages: List[Dict[str, Any]],
    agent_name: str = "unknown"
) -> None:
    """
    Detect duplicate patterns in assistant messages and log them.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        agent_name: Name of the agent for logging
    """
    # Extract assistant message contents
    assistant_messages = []
    for msg in messages:
        role = msg.get("role", "")
        if role != "assistant":
            continue
        
        content = extract_text_content(msg.get("content", ""))
        if len(content) > 20:  # Skip very short messages
            assistant_messages.append(content)
    
    if len(assistant_messages) < 2:
        return
    
    # Normalize and count patterns
    pattern_counts: Dict[str, Dict] = {}
    for msg in assistant_messages:
        normalized = normalize_for_comparison(msg)
        if not normalized:
            continue
        
        if normalized in pattern_counts:
            pattern_counts[normalized]["count"] += 1
            if len(pattern_counts[normalized]["samples"]) < 2:
                pattern_counts[normalized]["samples"].append(msg[:200])
        else:
            pattern_counts[normalized] = {
                "count": 1,
                "samples": [msg[:200]]
            }
    
    # Find duplicates (patterns appearing 2+ times)
    duplicates = []
    for normalized, data in pattern_counts.items():
        if data["count"] >= 2:
            duplicates.append({
                "pattern_hash": hash_pattern(normalized),
                "count": data["count"],
                "sample": data["samples"][0],
                "normalized_preview": normalized[:150]
            })
    
    if not duplicates:
        return
    
    # Write to daily log file
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"duplicates-{today}.jsonl"
    
    log_entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "agent": agent_name,
        "message_count": len(messages),
        "assistant_count": len(assistant_messages),
        "duplicates": duplicates
    }
    
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
        logger.debug(f"[duplicates] logged {len(duplicates)} patterns for {agent_name}")
    except Exception as e:
        logger.error(f"[duplicates] failed to write log: {e}")
