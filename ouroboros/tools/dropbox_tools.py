"""Dropbox document management tools for Ouroboros.

Tools:
  - dropbox_list_files: list files in a Dropbox folder
  - dropbox_download_file: download a file, save to /data/tmp/, return path + base64
  - dropbox_index_folder: scan folder, analyze with gpt-4o Vision, build index
  - dropbox_search_document: search index, download best match, send via Telegram
  - dropbox_show_index: show current index contents
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

_DROPBOX_FOLDER = "/vityai/ouroboros/docs"
_INDEX_PATH = Path("/data/docs_index.json")
_TMP_DIR = Path("/data/tmp")
_MAX_INLINE_BYTES = 5 * 1024 * 1024  # 5 MB
_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".gif", ".bmp", ".webp", ".heic", ".tiff"}
_VISION_PROMPT_PATH = Path(__file__).parent / "VISION_PROMPT.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_dbx():
    import dropbox  # noqa: PLC0415
    token = os.environ.get("DROPBOX_TOKEN", "")
    if not token:
        raise RuntimeError("DROPBOX_TOKEN not set")
    return dropbox.Dropbox(token)


def _load_index() -> list:
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_index(index: list) -> None:
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_vision_prompt() -> str:
    """Read VISION_PROMPT.md and extract the section starting with 'You are a document analysis expert.'"""
    try:
        text = _VISION_PROMPT_PATH.read_text(encoding="utf-8")
        marker = "You are a document analysis expert."
        idx = text.find(marker)
        if idx != -1:
            return text[idx:]
        return text
    except FileNotFoundError:
        return (
            "You are a document analysis expert. Analyze this document image and return ONLY valid JSON:\n"
            '{"type": "document type", "type_ru": "тип на русском", "description": "brief description", '
            '"person_names": [], "document_number": "", "issuer": "", '
            '"key_dates": {"issued": "", "expires": "", "other": ""}, '
            '"language": "ru/en/other", "country": "", "tags": []}'
        )


def _parse_json_safe(content: str, fallback: dict) -> dict:
    """Parse JSON from LLM response; on failure try to extract JSON substring."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        import re  # noqa: PLC0415
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        log.warning("Could not parse JSON from LLM response: %s", content[:200])
        return fallback


def _analyze_file_with_vision(file_bytes: bytes, filename: str) -> dict:
    """Use gpt-4o Vision to extract rich document metadata. Fallback on error."""
    empty_schema: dict = {
        "type": "unknown",
        "owner": None,
        "description": filename,
        "tags": [],
        "language": "",
    }
    _VISION_PROMPT = (
        "Проанализируй документ и извлеки данные в JSON.\n\n"
        "ВАЖНО для российского паспорта: поля расположены так:\n"
        "  Фамилия (первая строка) → Имя (вторая строка) → Отчество (третья строка) → Дата рождения → Пол → Место рождения → Гражданство\n"
        "  'УКРАИНСКОЙ ССР', 'РОССИЙСКОЙ ФЕДЕРАЦИИ', 'СССР' и подобное — это ГРАЖДАНСТВО или МЕСТО РОЖДЕНИЯ, НЕ ФАМИЛИЯ.\n"
        "  Фамилия и имя — это слова стоящие первыми, до даты рождения.\n\n"
        "Верни ТОЛЬКО валидный JSON:\n"
        "{\n"
        '  "type": "точный тип документа (паспорт РФ / СНИЛС / загранпаспорт / ОМС / ИНН / водительское удостоверение / свидетельство о рождении / другое)",\n'
        '  "owner": "Фамилия Имя Отчество владельца документа (или null если не видно). ТОЛЬКО ФИО, не гражданство и не место рождения.",\n'
        '  "description": "подробное описание: тип, владелец (ФИО), серия/номер (последние цифры скрой звёздочками), дата выдачи, кем выдан, место рождения, срок действия если есть",\n'
        '  "tags": ["тег1", "тег2", "tag_english"],\n'
        '  "language": "ru/en/другой"\n'
        "}\n"
        "Номера документов — заменяй последние 2-4 цифры на ** для безопасности."
    )
    try:
        import openai  # noqa: PLC0415
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return empty_schema

        client = openai.OpenAI(api_key=api_key)
        ext = Path(filename).suffix.lower()

        b64: str | None = None
        mime: str | None = None

        if ext == ".pdf":
            # Try pdf2image to render first page; fall back to filename-only analysis
            try:
                from pdf2image import convert_from_bytes  # noqa: PLC0415
                import io  # noqa: PLC0415
                pages = convert_from_bytes(file_bytes, first_page=1, last_page=1, dpi=150)
                if pages:
                    buf = io.BytesIO()
                    pages[0].save(buf, format="PNG")
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    mime = "image/png"
            except Exception:
                pass

            if b64 is None:
                # Filename-only fallback — return full structured schema
                fallback_prompt = (
                    f"Определи тип документа по имени файла: '{filename}'.\n"
                    "Верни ТОЛЬКО валидный JSON:\n"
                    '{"type": "тип документа", "owner": null, "description": "краткое описание", '
                    '"tags": ["тег1", "тег2"], "language": "ru"}'
                )
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": fallback_prompt}],
                    response_format={"type": "json_object"},
                    max_completion_tokens=400,
                )
                return _parse_json_safe(resp.choices[0].message.content, empty_schema)
        else:
            ext_mime = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
            }
            b64 = base64.b64encode(file_bytes).decode()
            mime = ext_mime.get(ext, f"image/{ext.lstrip('.')}")

        resp = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}},
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }],
            response_format={"type": "json_object"},
            max_completion_tokens=5000,
        )
        return _parse_json_safe(resp.choices[0].message.content, empty_schema)

    except Exception as e:
        log.warning("Vision analysis failed for %s: %s", filename, e)
        return empty_schema


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _dropbox_list_files(ctx: ToolContext, folder_path: str = "/") -> str:
    """List files and folders in a Dropbox folder."""
    try:
        import dropbox as dbx_mod  # noqa: PLC0415
        dbx = _get_dbx()
        result = dbx.files_list_folder(folder_path, recursive=False)
        entries = list(result.entries)
        while result.has_more:
            result = dbx.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)

        items = []
        for e in entries:
            if isinstance(e, dbx_mod.files.FileMetadata):
                items.append({
                    "path": e.path_lower,
                    "name": e.name,
                    "size": e.size,
                    "modified": e.server_modified.isoformat() if e.server_modified else "",
                    "type": "file",
                })
            elif isinstance(e, dbx_mod.files.FolderMetadata):
                items.append({
                    "path": e.path_lower,
                    "name": e.name,
                    "size": 0,
                    "modified": "",
                    "type": "folder",
                })

        return json.dumps(items, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("dropbox_list_files failed for '%s': %s", folder_path, e)
        return f"⚠️ Error listing Dropbox folder '{folder_path}': {e}"


def _dropbox_download_file(ctx: ToolContext, path: str) -> str:
    """Download a file from Dropbox, save to /data/tmp/, return local path + base64 for small files."""
    try:
        dbx = _get_dbx()
        _TMP_DIR.mkdir(parents=True, exist_ok=True)

        _metadata, response = dbx.files_download(path)
        file_bytes = response.content
        local_name = Path(path).name
        local_path = _TMP_DIR / local_name
        local_path.write_bytes(file_bytes)

        out: dict = {
            "local_path": str(local_path),
            "filename": local_name,
            "size": len(file_bytes),
        }

        if len(file_bytes) <= _MAX_INLINE_BYTES:
            out["base64"] = base64.b64encode(file_bytes).decode()
            out["note"] = "File included as base64 (< 5 MB)"
        else:
            out["note"] = "File > 5 MB — use local_path only."

        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        log.warning("dropbox_download_file failed for '%s': %s", path, e)
        return f"⚠️ Error downloading '{path}': {e}"


def _dropbox_index_folder(ctx: ToolContext, folder_path: str = _DROPBOX_FOLDER) -> str:
    """Scan Dropbox folder, analyze each file with Vision AI, build/update /data/docs_index.json."""
    try:
        import dropbox as dbx_mod  # noqa: PLC0415
        dbx = _get_dbx()

        try:
            result = dbx.files_list_folder(folder_path, recursive=True)
            entries = list(result.entries)
            while result.has_more:
                result = dbx.files_list_folder_continue(result.cursor)
                entries.extend(result.entries)
        except Exception as list_err:
            if "not_found" in str(list_err).lower():
                try:
                    dbx.files_create_folder_v2(folder_path)
                    return json.dumps({
                        "status": "ok",
                        "message": f"Folder '{folder_path}' created. Upload documents there and re-index.",
                        "indexed": 0, "skipped": 0, "total": 0, "errors": [],
                    })
                except Exception as create_err:
                    return f"⚠️ Folder not found and could not be created: {create_err}"
            return f"⚠️ Error listing folder '{folder_path}': {list_err}"

        file_entries = [
            e for e in entries
            if isinstance(e, dbx_mod.files.FileMetadata)
            and Path(e.name).suffix.lower() in _SUPPORTED_EXTENSIONS
        ]

        existing_index = _load_index()
        existing_by_path = {item["path"]: item for item in existing_index}

        new_index: list = []
        indexed_count = 0
        skipped_count = 0
        errors: list = []

        for entry in file_entries:
            path = entry.path_lower
            modified = entry.server_modified.isoformat() if entry.server_modified else ""

            existing = existing_by_path.get(path)
            if existing and existing.get("modified") == modified:
                new_index.append(existing)
                skipped_count += 1
                continue

            try:
                _, dl_resp = dbx.files_download(path)
                file_bytes = dl_resp.content
                analysis = _analyze_file_with_vision(file_bytes, entry.name)
                new_index.append({
                    "path": path,
                    "name": entry.name,
                    "size": entry.size,
                    "modified": modified,
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                    "type": analysis.get("type", "unknown"),
                    "owner": analysis.get("owner"),
                    "description": analysis.get("description", entry.name),
                    "tags": analysis.get("tags", []),
                    "language": analysis.get("language", ""),
                })
                indexed_count += 1
                log.info("Indexed: %s → %s", entry.name, analysis.get("type"))
            except Exception as idx_err:
                log.error("Error indexing %s: %s", entry.name, idx_err)
                errors.append(f"{entry.name}: {idx_err}")
                # Add with basic metadata so the file appears in the index
                new_index.append({
                    "path": path,
                    "name": entry.name,
                    "size": entry.size,
                    "modified": modified,
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                    "type": "unknown",
                    "owner": None,
                    "description": entry.name,
                    "tags": [],
                    "language": "",
                })

        _save_index(new_index)

        summary = (
            f"Indexed: {indexed_count} new, {skipped_count} unchanged, "
            f"{len(new_index)} total in '{folder_path}'"
        )
        if errors:
            summary += f". Errors: {'; '.join(errors[:5])}"

        return json.dumps({
            "status": "ok",
            "message": summary,
            "indexed": indexed_count,
            "skipped": skipped_count,
            "total": len(new_index),
            "errors": errors,
        }, ensure_ascii=False)

    except Exception as e:
        log.warning("dropbox_index_folder failed: %s", e, exc_info=True)
        return f"⚠️ Error indexing folder '{folder_path}': {e}"


def _dropbox_search_document(ctx: ToolContext, query: str) -> str:
    """Search index for best match, download from Dropbox, send to user via Telegram."""
    if not ctx.current_chat_id:
        return "⚠️ No active chat — cannot send document."

    index = _load_index()
    if not index:
        return "⚠️ Index is empty. Run dropbox_index_folder first to scan documents."

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "⚠️ OPENAI_API_KEY not set — cannot perform semantic search."

    # Find best match via LLM
    try:
        import openai  # noqa: PLC0415
        client = openai.OpenAI(api_key=api_key)

        catalog = "\n".join([
            f"{i}. {item['type']} — владелец: {item.get('owner', '?')} — {item['description']}"
            f" | Tags: {', '.join(item.get('tags', []))}"
            for i, item in enumerate(index)
        ])
        prompt = (
            f"User documents:\n{catalog}\n\n"
            f"User query: \"{query}\"\n\n"
            "Find the most relevant document. Reply in JSON:\n"
            "{\"found\": true/false, \"index\": <number or null>, \"reasoning\": \"brief reason\"}\n\n"
            "If multiple match, pick the most relevant. If not found, use found: false."
        )
        resp = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_completion_tokens=200,
        )
        match = json.loads(resp.choices[0].message.content)
    except Exception as e:
        log.warning("dropbox_search_document LLM search failed: %s", e)
        return f"⚠️ Search failed: {e}"

    if not match.get("found") or match.get("index") is None:
        doc_list = "\n".join([f"• {item['type']}: {item['description']}" for item in index])
        return f"No document found for query '{query}'.\n\nAvailable:\n{doc_list}"

    doc = index[int(match["index"])]

    # Download from Dropbox
    try:
        dbx = _get_dbx()
        _, dl_resp = dbx.files_download(doc["path"])
        file_bytes = dl_resp.content
    except Exception as e:
        log.warning("dropbox_search_document download failed for '%s': %s", doc["path"], e)
        return f"⚠️ Found '{doc['name']}' but download failed: {e}"

    # Emit event to send document via Telegram
    file_b64 = base64.b64encode(file_bytes).decode()
    caption = f"{doc['type']}: {doc['description']}"
    ctx.pending_events.append({
        "type": "send_document",
        "chat_id": ctx.current_chat_id,
        "file_base64": file_b64,
        "filename": doc["name"],
        "caption": caption,
    })

    return (
        f"OK: document '{doc['name']}' ({doc['type']}) queued for delivery.\n"
        f"Reason: {match.get('reasoning', '')}"
    )


def _dropbox_show_index(ctx: ToolContext) -> str:
    """Show current document index in readable format."""
    index = _load_index()
    if not index:
        return "Index is empty. Run dropbox_index_folder to scan documents."

    lines = [f"Document index — {len(index)} item(s):"]
    for i, item in enumerate(index):
        tags = ", ".join(item.get("tags", [])) or "—"
        size_kb = item.get("size", 0) // 1024
        modified = (item.get("modified") or "")[:10]
        indexed_at = (item.get("indexed_at") or "")[:10]
        entry_lines = [
            f"\n{i + 1}. [{item.get('type', '?')}] {item['name']}",
            f"   Description: {item.get('description', '—')}",
        ]
        if item.get("owner"):
            entry_lines.append(f"   Owner: {item['owner']}")
        entry_lines.append(f"   Tags: {tags}")
        entry_lines.append(f"   Size: {size_kb} KB | Modified: {modified} | Indexed: {indexed_at}")
        entry_lines.append(f"   Path: {item['path']}")
        lines.append("\n".join(entry_lines))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="dropbox_list_files",
            schema={
                "name": "dropbox_list_files",
                "description": (
                    "List files and folders in a Dropbox folder. "
                    "Returns JSON array with path, name, size, modified, type fields."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder_path": {
                            "type": "string",
                            "description": "Dropbox folder path to list (default: '/')",
                        },
                    },
                    "required": [],
                },
            },
            handler=_dropbox_list_files,
            timeout_sec=30,
        ),
        ToolEntry(
            name="dropbox_download_file",
            schema={
                "name": "dropbox_download_file",
                "description": (
                    "Download a file from Dropbox. Saves to /data/tmp/ and returns the local path. "
                    "For files < 5 MB, also returns base64-encoded file content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Dropbox file path to download (e.g. '/Ouroboros/docs/passport.jpg')",
                        },
                    },
                    "required": ["path"],
                },
            },
            handler=_dropbox_download_file,
            timeout_sec=60,
        ),
        ToolEntry(
            name="dropbox_index_folder",
            schema={
                "name": "dropbox_index_folder",
                "description": (
                    "Scan a Dropbox folder and build/update the document index using Vision AI (gpt-4o). "
                    "For each file: determines document type, extracts key info (name, number/serial, dates, issuer). "
                    "Index is stored at /data/docs_index.json. "
                    "Call when user adds new documents or asks to re-index."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder_path": {
                            "type": "string",
                            "description": "Dropbox folder to scan and index (default: '/Ouroboros/docs')",
                        },
                    },
                    "required": [],
                },
            },
            handler=_dropbox_index_folder,
            timeout_sec=300,
        ),
        ToolEntry(
            name="dropbox_search_document",
            schema={
                "name": "dropbox_search_document",
                "description": (
                    "Search the document index for a user query, download the best matching document "
                    "from Dropbox, and send it to the user via Telegram. "
                    "Use when the user asks for a specific document: 'passport', 'insurance', 'driving license', etc. "
                    "Requires dropbox_index_folder to have been run at least once."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "User's document request — what they want to find",
                        },
                    },
                    "required": ["query"],
                },
            },
            handler=_dropbox_search_document,
            timeout_sec=60,
        ),
        ToolEntry(
            name="dropbox_show_index",
            schema={
                "name": "dropbox_show_index",
                "description": (
                    "Show the current document index in a readable format. "
                    "Use to list what documents are indexed or to show the user their document collection."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            handler=_dropbox_show_index,
            timeout_sec=10,
        ),
    ]
