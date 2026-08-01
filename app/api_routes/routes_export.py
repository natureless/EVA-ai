"""Conversation export/import API.

Exports chat history as Markdown or JSON. Supports importing
conversations to restore context across sessions.
"""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("eva.api.export")

router = APIRouter()


# ── Models ──────────────────────────────────────────────────

class ConversationImport(BaseModel):
    """Payload for importing a conversation."""
    format: str = Field(default="json", pattern="^(json|markdown)$")
    content: str = Field(min_length=1, max_length=1_000_000)
    source_label: str = Field(default="imported", max_length=100)


# ── Export ──────────────────────────────────────────────────

@router.get("/api/conversation/export")
def export_conversation(
    request: Request,
    fmt: str = Query(default="json", alias="format", pattern="^(json|markdown)$"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> Any:
    """Export recent conversation history.

    Args:
        fmt: Output format — \"json\" or \"markdown\".
        limit: Maximum number of messages to include.
    """
    container = request.app.state.container
    store = getattr(container, "store", None)
    if store is None:
        return JSONResponse(
            status_code=503,
            content={"error": "storage backend not available"},
        )

    try:
        rows = store.fetchall(
            "SELECT type, source, payload, timestamp FROM events "
            "WHERE type IN ('user_message', 'agent_response', 'reminder_trigger') "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
    except Exception as e:
        logger.exception("export query failed")
        return JSONResponse(status_code=500, content={"error": str(e)})

    # Reverse to chronological order
    rows = list(reversed(rows))

    if fmt == "markdown":
        return _export_markdown(rows)
    return _export_json(rows)


def _export_json(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Export as structured JSON."""
    messages = []
    for row in rows:
        payload = row.get("payload", "")
        if isinstance(payload, str):
            try:
                payload = _json.loads(payload)
            except (_json.JSONDecodeError, TypeError):
                pass

        text = ""
        if isinstance(payload, dict):
            text = payload.get("text", payload.get("reply", payload.get("message", "")))
        elif isinstance(payload, str):
            text = payload

        messages.append({
            "type": row["type"],
            "source": row.get("source", ""),
            "text": str(text)[:5000],
            "timestamp": str(row.get("timestamp", "")),
        })

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format": "json",
        "message_count": len(messages),
        "messages": messages,
    }


def _export_markdown(rows: list[dict[str, Any]]) -> PlainTextResponse:
    """Export as human-readable Markdown."""
    lines = [
        "# EVA Conversation Export",
        "",
        f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Messages: {len(rows)}",
        "",
        "---",
        "",
    ]

    for row in rows:
        payload = row.get("payload", "")
        if isinstance(payload, str):
            try:
                payload = _json.loads(payload)
            except (_json.JSONDecodeError, TypeError):
                pass

        text = ""
        if isinstance(payload, dict):
            text = payload.get("text", payload.get("reply", payload.get("message", "")))
        elif isinstance(payload, str):
            text = payload

        role = "**User**" if row["type"] == "user_message" else "**EVA**"
        ts = str(row.get("timestamp", ""))[:19]
        lines.append(f"### {role} — {ts}")
        lines.append("")
        lines.append(str(text)[:5000])
        lines.append("")
        lines.append("---")
        lines.append("")

    return PlainTextResponse(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=eva-conversation.md"},
    )


# ── Import ──────────────────────────────────────────────────

@router.post("/api/conversation/import")
def import_conversation(payload: ConversationImport, request: Request) -> dict[str, Any]:
    """Import a conversation to restore context.

    Parses the provided content and writes messages to the event store
    so they appear in memory and world model.
    """
    container = request.app.state.container
    store = getattr(container, "store", None)
    if store is None:
        return {"ok": False, "error": "storage backend not available"}

    try:
        if payload.format == "json":
            data = _json.loads(payload.content)
            messages = data.get("messages", [])
        else:
            # Parse markdown — extract sections between ### markers
            messages = _parse_markdown_messages(payload.content)
    except Exception as e:
        return {"ok": False, "error": f"parse error: {e}"}

    imported = 0
    for msg in messages:
        try:
            evt_type = msg.get("type", "user_message")
            text = msg.get("text", "")
            if not text.strip():
                continue

            store.execute(
                "INSERT INTO events (id, type, source, timestamp, payload, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"import_{datetime.now(timezone.utc).timestamp()}_{imported}",
                    evt_type,
                    payload.source_label,
                    datetime.now(timezone.utc).isoformat(),
                    _json.dumps({"text": text, "imported": True}),
                    "imported",
                ),
            )
            imported += 1
        except Exception as e:
            logger.warning("import skipped message: %s", e)

    return {
        "ok": True,
        "imported": imported,
        "source_label": payload.source_label,
        "summary": f"Imported {imported} messages from {payload.format} format",
    }


def _parse_markdown_messages(content: str) -> list[dict[str, Any]]:
    """Parse markdown export format back into message dicts."""
    import re

    messages = []
    # Split on ### headers
    sections = re.split(r"\n###\s+", content)
    for section in sections:
        if not section.strip():
            continue
        # Determine role from the header
        role = "user_message"
        if section.startswith("**EVA**"):
            role = "agent_response"
        elif section.startswith("**User**"):
            role = "user_message"
        else:
            continue

        # Extract text after the header line
        lines = section.split("\n", 1)
        text = lines[1].strip() if len(lines) > 1 else ""
        # Remove trailing ---
        text = re.sub(r"\n---\s*$", "", text)

        if text.strip():
            messages.append({"type": role, "text": text.strip()})

    return messages
