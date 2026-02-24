"""Dropbox document management tools for Ouroboros.

Tools:
  - dropbox_list_files: list files in a Dropbox folder
  - dropbox_download_file: download a file, save to /data/tmp/, return path + base64
  - dropbox_index_folder: scan folder, analyze with Gemini Vision, build index
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
from typing import List, Optional

from ouroboros.llm import LLMClient
from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

_DROPBOX_FOLDER = "/vityai/ouroboros/docs"
_INDEX_PATH = Path("/data/docs_index.json")
_CURSOR_PATH = Path("/data/dropbox_cursor.json")
_TMP_DIR = Path("/data/tmp")
_MAX_INLINE_BYTES = 5 * 1024 * 1024  # 5 MB
_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".gif", ".bmp", ".webp", ".heic", ".tiff"}
_VISION_PROMPT_PATH = Path(__file__).parent / "VISION_PROMPT.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_dbx():
    """Create authenticated Dropbox client.

    Prefers long-lived refresh token auth (DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY).
    Falls back to legacy short-lived DROPBOX_TOKEN.
    """
    import dropbox  # noqa: PLC0415
    refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN", "").strip()
    app_key = os.environ.get("DROPBOX_APP_KEY", "").strip()
    legacy_token = os.environ.get("DROPBOX_TOKEN", "").strip()

    if refresh_token and app_key:
        return dropbox.Dropbox(
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
        )
    elif legacy_token:
        return dropbox.Dropbox(legacy_token)
    else:
        raise RuntimeError(
            "Dropbox auth not configured. "
            "Set DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY (recommended) "
            "or DROPBOX_TOKEN (legacy, expires)."
        )


def _is_auth_error(e: Exception) -> bool:
    """Return True if the exception is a Dropbox auth/expired-token error."""
    cls_name = type(e).__name__.lower()
    err_str = str(e).lower()
    return (
        "autherror" in cls_name
        or "expired_access_token" in err_str
        or "auth_error" in err_str
        or "invalid_access_token" in err_str
    )


_AUTH_HELP_MESSAGE = """⚠️ Dropbox auth failed (token expired or not configured).

To set up persistent auth (one-time):

1. Go to https://www.dropbox.com/developers/apps → your app → Settings
   Copy: App key + App secret

2. Run this Python script locally to get a refresh token:

    from dropbox import DropboxOAuth2FlowNoRedirect
    APP_KEY = "your_app_key"
    APP_SECRET = "your_app_secret"
    flow = DropboxOAuth2FlowNoRedirect(
        APP_KEY, APP_SECRET,
        token_access_type="offline",
        use_pkce=True,
    )
    print("Open URL:", flow.start())
    code = input("Paste code: ").strip()
    result = flow.finish(code)
    print("DROPBOX_REFRESH_TOKEN =", result.refresh_token)

3. Add to .env:
   DROPBOX_APP_KEY=your_app_key
   DROPBOX_REFRESH_TOKEN=the_refresh_token

4. Restart the agent.
"""


def _load_cursor() -> Optional[str]:
    """Load persisted Dropbox cursor from disk. Returns None if not found."""
    try:
        data = json.loads(_CURSOR_PATH.read_text(encoding="utf-8"))
        return data.get("cursor")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cursor(cursor: str) -> None:
    """Persist Dropbox cursor to disk for incremental polling."""
    _CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CURSOR_PATH.write_text(
        json.dumps({"cursor": cursor, "ts": datetime.now(timezone.utc).isoformat(), "folder": _DROPBOX_FOLDER},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_index() -> list:
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_index(index: list) -> None:
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_vision_prompt() -> str:
    """Load the Vision analysis prompt from VISION_PROMPT.md.

    The file has a Markdown header section separated from the actual prompt
    by a ``---`` line.  Everything after the first ``---`` is returned as-is.
    Falls back to a minimal schema-complete prompt if the file is missing.
    """
    try:
        text = _VISION_PROMPT_PATH.read_text(encoding="utf-8")
        # Strip the header comment block (everything up to and including the
        # first horizontal-rule separator line).
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            return parts[1].strip()
        # No separator found — return the whole file (shouldn't happen in practice)
        return text.strip()
    except FileNotFoundError:
        log.warning(
            "VISION_PROMPT.md not found at %s — using built-in fallback prompt",
            _VISION_PROMPT_PATH,
        )
        return (
            "You are a document analysis expert. Analyze this document image and "
            "return ONLY a valid JSON object (no markdown, no explanation):\n"
            '{"type": "document type in Russian", "type_en": "document type in English", '
            '"owner": "full name or null", "person_names": [], '
            '"description": "brief description", '
            '"document_number": "masked with **", "issuer": "", "country": "", '
            '"language": "ru/en/other", '
            '"key_dates": {"issued": "", "expires": "", "birth": "", "other": ""}, '
            '"tags": [], "ocr_raw": ""}'
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


def _build_index_entry(path: str, name: str, size: int, modified: str, analysis: dict) -> dict:
    """Build a canonical index entry from Vision analysis result."""
    kd = analysis.get("key_dates", {})
    if not isinstance(kd, dict):
        kd = {}
    return {
        "path": path,
        "name": name,
        "size": size,
        "modified": modified,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "type": analysis.get("type", "unknown"),
        "type_en": analysis.get("type_en", ""),
        "owner": analysis.get("owner"),
        "person_names": analysis.get("person_names", []),
        "description": analysis.get("description", name),
        "document_number": analysis.get("document_number", ""),
        "issuer": analysis.get("issuer", ""),
        "country": analysis.get("country", ""),
        "language": analysis.get("language", ""),
        "key_dates": {
            "issued": kd.get("issued", ""),
            "expires": kd.get("expires", ""),
            "birth": kd.get("birth", ""),
            "other": kd.get("other", ""),
        },
        "tags": analysis.get("tags", []),
        "ocr_raw": analysis.get("ocr_raw", ""),
    }


def _analyze_file_with_vision(file_bytes: bytes, filename: str) -> dict:
    """Use Gemini Vision to extract rich document metadata. Fallback on error."""
    empty_schema: dict = {
        "type": "unknown",
        "owner": None,
        "description": filename,
        "tags": [],
        "language": "",
    }
    vision_prompt = _load_vision_prompt()
    try:
        llm = LLMClient()
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
                text, _usage = llm.vision_query(
                    prompt=fallback_prompt,
                    images=[],
                    model="google/gemini-2.0-flash",
                    max_tokens=400,
                )
                return _parse_json_safe(text, empty_schema)
        else:
            ext_mime = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
            }
            mime = ext_mime.get(ext, f"image/{ext.lstrip('.')}")

            # Compress large images before sending to Vision API
            if len(file_bytes) > 2 * 1024 * 1024:
                try:
                    from PIL import Image  # noqa: PLC0415
                    import io  # noqa: PLC0415
                    old_size = len(file_bytes)
                    img = Image.open(io.BytesIO(file_bytes))
                    img = img.convert("RGB")
                    # Resize if too large
                    max_dim = 2000
                    if img.width > max_dim or img.height > max_dim:
                        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85, optimize=True)
                    file_bytes = buf.getvalue()
                    mime = "image/jpeg"
                    log.info("Compressed image from %d to %d bytes", old_size, len(file_bytes))
                except Exception as compress_err:
                    log.warning("Could not compress image: %s", compress_err)

            b64 = base64.b64encode(file_bytes).decode()

        text, _usage = llm.vision_query(
            prompt=vision_prompt,
            images=[{"base64": b64, "mime": mime}],
            model="google/gemini-2.0-flash",
            max_tokens=1000,
        )
        return _parse_json_safe(text, empty_schema)

    except Exception as e:
        log.warning("Vision analysis failed for %s: %s", filename, e)
        return empty_schema


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _dropbox_auth_status(ctx: ToolContext) -> str:
    """Check Dropbox authentication status and return a JSON status dict."""
    refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN", "").strip()
    app_key = os.environ.get("DROPBOX_APP_KEY", "").strip()
    legacy_token = os.environ.get("DROPBOX_TOKEN", "").strip()

    if refresh_token and app_key:
        auth_method = "refresh_token"
        configured = True
    elif legacy_token:
        auth_method = "legacy_token"
        configured = True
    else:
        auth_method = "none"
        configured = False

    if not configured:
        return json.dumps({
            "configured": False,
            "auth_method": "none",
            "status": "not_configured",
            "account_name": None,
            "message": _AUTH_HELP_MESSAGE,
        }, ensure_ascii=False)

    try:
        dbx = _get_dbx()
        account = dbx.users_get_current_account()
        return json.dumps({
            "configured": True,
            "auth_method": auth_method,
            "status": "ok",
            "account_name": account.name.display_name,
            "email": account.email,
            "message": "✅ Dropbox auth is working correctly.",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "configured": True,
            "auth_method": auth_method,
            "status": "expired" if _is_auth_error(e) else "error",
            "account_name": None,
            "message": _AUTH_HELP_MESSAGE if _is_auth_error(e) else f"Error: {e}",
        }, ensure_ascii=False)


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
        if _is_auth_error(e):
            return _AUTH_HELP_MESSAGE
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
        if _is_auth_error(e):
            return _AUTH_HELP_MESSAGE
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
                idx_entry = _build_index_entry(path, entry.name, entry.size, modified, analysis)
                idx_entry["rev"] = entry.rev
                new_index.append(idx_entry)
                indexed_count += 1
                log.info("Indexed: %s → %s", entry.name, analysis.get("type"))
            except Exception as idx_err:
                log.error("Error indexing %s: %s", entry.name, idx_err)
                errors.append(f"{entry.name}: {idx_err}")
                entry_data = _build_index_entry(path, entry.name, entry.size, modified, {})
                entry_data["error"] = str(idx_err)
                new_index.append(entry_data)

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
        if _is_auth_error(e):
            return _AUTH_HELP_MESSAGE
        return f"⚠️ Error indexing folder '{folder_path}': {e}"


def _dropbox_search_document(ctx: ToolContext, query: str) -> str:
    """Search index for best match, download from Dropbox, send to user via Telegram."""
    if not ctx.current_chat_id:
        return "⚠️ No active chat — cannot send document."

    index = _load_index()
    if not index:
        return "⚠️ Index is empty. Run dropbox_index_folder first to scan documents."

    # Find best match via LLM
    try:
        llm = LLMClient()

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
        response_msg, _usage = llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model="google/gemini-2.0-flash",
            max_tokens=3000,
        )
        text = response_msg.get("content") or ""
        match = _parse_json_safe(text, {"found": False, "index": None, "reasoning": "parse error"})
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
        if _is_auth_error(e):
            return _AUTH_HELP_MESSAGE
        return f"⚠️ Found '{doc['name']}' but download failed: {e}"

    # Emit event to send document via Telegram
    file_b64 = base64.b64encode(file_bytes).decode()
    _type_labels = {
        "паспорт рф": "📄 Паспорт",
        "снилс": "📄 СНИЛС",
        "загранпаспорт": "📄 Загранпаспорт",
        "водительское удостоверение": "🪪 Права",
        "омс": "📄 Полис ОМС",
    }
    caption = _type_labels.get(doc["type"].lower(), "📄 Документ")
    ctx.pending_events.append({
        "type": "send_document",
        "chat_id": ctx.current_chat_id,
        "file_base64": file_b64,
        "filename": doc["name"],
        "caption": caption,
    })

    return "sent"


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


def _process_changed_entries(entries: list, dbx) -> list:
    """Process Dropbox delta entries: index new/changed files, remove deleted ones."""
    import dropbox as dbx_mod  # noqa: PLC0415

    existing_index = _load_index()
    index_by_path = {item["path"]: item for item in existing_index}

    new_files = []
    modified_paths = set()

    for entry in entries:
        path = entry.path_lower

        # Deletion event
        if isinstance(entry, dbx_mod.files.DeletedMetadata):
            if path in index_by_path:
                del index_by_path[path]
                log.info("Removed deleted file from index: %s", path)
            continue

        if not isinstance(entry, dbx_mod.files.FileMetadata):
            continue  # skip folders

        ext = Path(entry.name).suffix.lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            continue

        if entry.name.startswith((".", "~", "_")) or entry.name.endswith(".tmp"):
            continue  # skip temp/hidden files

        # Rev-based dedup: skip if already indexed with same revision
        existing = index_by_path.get(path)
        if existing and existing.get("rev") == entry.rev:
            log.debug("Skipping unchanged file (same rev): %s", path)
            continue

        modified_paths.add(path)

        # Download + Vision analysis
        try:
            _, dl_resp = dbx.files_download(path)
            file_bytes = dl_resp.content
        except Exception as dl_err:
            log.warning("Could not download %s: %s", path, dl_err)
            continue

        modified = entry.server_modified.isoformat() if entry.server_modified else ""
        analysis = _analyze_file_with_vision(file_bytes, entry.name)

        index_entry = _build_index_entry(path, entry.name, entry.size, modified, analysis)
        index_entry["rev"] = entry.rev  # store rev for future dedup

        index_by_path[path] = index_entry
        new_files.append({"name": entry.name, "type": analysis.get("type", "unknown"), "path": path})
        log.info("Auto-indexed: %s → %s", entry.name, analysis.get("type"))

    # Save updated index
    _save_index(list(index_by_path.values()))

    return new_files


def _dropbox_check_updates(ctx: ToolContext) -> str:
    """Poll Dropbox for new/changed files since last cursor. Auto-index any new documents."""
    try:
        import dropbox as dbx_mod  # noqa: PLC0415
        dbx = _get_dbx()

        # --- Bootstrap if no cursor ---
        cursor = _load_cursor()
        if cursor is None:
            log.info("dropbox_check_updates: no cursor — bootstrapping from folder listing")
            try:
                result = dbx.files_list_folder(_DROPBOX_FOLDER, recursive=True)
            except Exception as list_err:
                if "not_found" in str(list_err).lower():
                    try:
                        dbx.files_create_folder_v2(_DROPBOX_FOLDER)
                        log.info("Created Dropbox folder: %s", _DROPBOX_FOLDER)
                    except Exception:
                        pass
                    _save_cursor("")
                    return json.dumps({"status": "bootstrapped", "new_files": [], "message": f"Folder {_DROPBOX_FOLDER} not found, created. Drop documents there."})
                return json.dumps({"status": "error", "message": str(list_err)})
            _save_cursor(result.cursor)
            # Process any existing files as "new" on first run
            all_entries = list(result.entries)
            while result.has_more:
                result = dbx.files_list_folder_continue(result.cursor)
                all_entries.extend(result.entries)
                _save_cursor(result.cursor)
            if all_entries:
                new_files = _process_changed_entries(all_entries, dbx)
                return json.dumps({"status": "bootstrapped", "new_files": new_files, "message": f"Initial scan: {len(new_files)} document(s) indexed."})
            return json.dumps({"status": "bootstrapped", "new_files": [], "message": "Folder is empty — drop documents to index them automatically."})

        # --- Cursor too old or corrupted? ---
        if not cursor:
            # cursor was saved as empty string (folder didn't exist at bootstrap time)
            # try again from scratch
            _CURSOR_PATH.unlink(missing_ok=True)
            return _dropbox_check_updates(ctx)

        # --- Normal incremental poll ---
        try:
            result = dbx.files_list_folder_continue(cursor)
        except Exception as cont_err:
            err_str = str(cont_err).lower()
            if "reset" in err_str or "expired" in err_str or "malformed_cursor" in err_str:
                log.warning("Dropbox cursor reset/expired — re-bootstrapping")
                _CURSOR_PATH.unlink(missing_ok=True)
                return _dropbox_check_updates(ctx)
            return json.dumps({"status": "error", "message": str(cont_err)})

        all_entries = list(result.entries)
        while result.has_more:
            result = dbx.files_list_folder_continue(result.cursor)
            all_entries.extend(result.entries)
        _save_cursor(result.cursor)

        if not all_entries:
            return json.dumps({"status": "no_changes", "new_files": []})

        new_files = _process_changed_entries(all_entries, dbx)
        msg = f"{len(new_files)} new/updated document(s) auto-indexed." if new_files else "Changes detected but no new documents to index."
        return json.dumps({"status": "ok", "new_files": new_files, "message": msg})

    except Exception as e:
        log.error("dropbox_check_updates failed: %s", e, exc_info=True)
        if _is_auth_error(e):
            return _AUTH_HELP_MESSAGE
        return json.dumps({"status": "error", "message": str(e)})


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
                    "Scan a Dropbox folder and build/update the document index using Vision AI (Gemini Vision). "
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
        ToolEntry(
            name="dropbox_auth_status",
            schema={
                "name": "dropbox_auth_status",
                "description": "Check Dropbox authentication status and get setup instructions if auth is broken",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            handler=_dropbox_auth_status,
            timeout_sec=15,
        ),
        ToolEntry(
            name="dropbox_check_updates",
            schema={
                "name": "dropbox_check_updates",
                "description": (
                    "Check Dropbox for new or changed files since the last poll. "
                    "Uses a persistent cursor for efficient incremental detection — only fetches deltas. "
                    "Auto-downloads and indexes any new documents using Vision AI. "
                    "Call periodically (e.g. every 5 minutes) or when user asks to check for new documents. "
                    "On first call, bootstraps the cursor by scanning the entire folder."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            handler=_dropbox_check_updates,
            timeout_sec=300,
        ),
    ]
