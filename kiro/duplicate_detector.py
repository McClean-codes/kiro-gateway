# -*- coding: utf-8 -*-
"""
Duplicate message detection for token discipline.

Scans assistant messages for repeating patterns and logs them for review.
Patterns are normalized by stripping UUIDs, request IDs, timestamps, etc.
Uses fuzzy matching (Jaccard similarity) to catch near-duplicates.
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set
from loguru import logger

LOGS_DIR = Path(__file__).parent.parent / "logs"
SIMILARITY_THRESHOLD = 0.85  # 85% similarity = duplicate


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
    # Strip retry/attempt counters (1 out of 2, 2/3, attempt 1, iteration 5, etc.)
    result = re.sub(r'\b\d+\s*(out of|of|/)\s*\d+', '__RETRY__', result, flags=re.IGNORECASE)
    result = re.sub(r'\b(attempt|retry|try|iteration|step)\s*#?\d+', r'\1 __N__', result, flags=re.IGNORECASE)
    # Strip standalone numbers that look like counters or codes
    result = re.sub(r'\b\d{1,5}\b', '__N__', result)
    # Normalize whitespace
    result = re.sub(r'\s+', ' ', result).strip()
    
    return result


def hash_pattern(text: str) -> str:
    """Generate a short hash for a normalized pattern."""
    return hashlib.md5(text.encode()).hexdigest()[:12]


def tokenize(text: str) -> Set[str]:
    """Split text into lowercase word tokens."""
    return set(word.lower() for word in text.split() if word)


def jaccard_similarity(tokens1: Set[str], tokens2: Set[str]) -> float:
    """Calculate Jaccard similarity between two token sets."""
    if not tokens1 and not tokens2:
        return 0.0
    intersection = len(tokens1 & tokens2)
    union = len(tokens1 | tokens2)
    return intersection / union if union > 0 else 0.0


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
    Uses fuzzy matching with 85% Jaccard similarity threshold.
    
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
    
    # Normalize messages and tokenize
    normalized_items = []
    for msg in assistant_messages:
        norm = normalize_for_comparison(msg)
        normalized_items.append({
            "original": msg,
            "normalized": norm,
            "tokens": tokenize(norm)
        })
    
    # Group similar messages using fuzzy matching
    groups: List[Dict] = []  # List of { representative, tokens, members }
    
    for item in normalized_items:
        if not item["normalized"]:
            continue
        
        # Find existing group with >= 85% similarity
        found_group = None
        for group in groups:
            similarity = jaccard_similarity(item["tokens"], group["tokens"])
            if similarity >= SIMILARITY_THRESHOLD:
                found_group = group
                break
        
        if found_group:
            found_group["members"].append(item)
        else:
            groups.append({
                "representative": item["normalized"],
                "tokens": item["tokens"],
                "members": [item]
            })
    
    # Log groups with 2+ members (duplicates)
    duplicates = []
    for group in groups:
        if len(group["members"]) >= 2:
            duplicates.append({
                "pattern_hash": hash_pattern(group["representative"]),
                "count": len(group["members"]),
                "sample": group["members"][0]["original"][:200],
                "normalized_preview": group["representative"][:150]
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
