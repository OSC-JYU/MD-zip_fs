from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
import uuid
import json
import zipfile
import shutil
import time
from typing import Any, Dict, List, Optional
import logging

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
MD_URL = os.getenv("MD_URL", "http://localhost:8200")
MD_PATH_ENV = os.getenv("MD_PATH", "")
CONTAINER_MODE = os.getenv("CONTAINER", "").strip().lower() in ("1", "true", "yes", "on")
STORAGE_MODE = (os.getenv("STORAGE_MODE") or os.getenv("FILE_STORAGE_MODE") or "disk").strip().lower()
DEFAULT_ALLOWED_EXTENSIONS = ('txt', 'jpg', 'jpeg', 'png', 'pdf', 'json')
REQUEST_READ_CHUNK_SIZE = int(os.getenv("REQUEST_READ_CHUNK_SIZE", str(1024 * 1024)))
COPY_CHUNK_SIZE = int(os.getenv("COPY_CHUNK_SIZE", str(1024 * 1024)))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("md-zip-fs")


def resolve_md_root(md_path_env: str, container_mode: bool) -> str:
    """Resolve runtime MessyDesk root across host/container layouts.

    Expected root is the directory that contains the `data/` directory.
    """
    if STORAGE_MODE == "disk" and (not isinstance(md_path_env, str) or not md_path_env.strip()):
        raise RuntimeError("MD_PATH must be set when STORAGE_MODE=disk")

    candidates = []
    if isinstance(md_path_env, str) and md_path_env.strip():
        raw = os.path.abspath(md_path_env.strip())
        # Accept either repository root or direct data directory.
        if os.path.basename(raw) == 'data':
            candidates.append(os.path.dirname(raw))
        candidates.append(raw)

    # Container mode is explicit.
    if container_mode:
        candidates.append('/app')

    cwd = os.path.abspath('.')
    candidates.append(cwd)

    seen = set()
    existing_dirs = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isdir(os.path.join(candidate, 'data')):
            return candidate
        if os.path.isdir(candidate):
            existing_dirs.append(candidate)

    # If caller gave a valid directory but data/ is created later, accept it.
    if existing_dirs:
        return existing_dirs[0]

    raise RuntimeError(
        "Could not resolve MessyDesk data root. Set MD_PATH to the MessyDesk root "
        "(contains data/). If running in container, set CONTAINER=true and MD_PATH=/app."
    )


try:
    MD_ROOT = resolve_md_root(MD_PATH_ENV, CONTAINER_MODE)
except RuntimeError as err:
    print(f"ERROR: {err} \nexiting...")
    exit(1)

app = FastAPI(
    title="zip API",
    description="API for zip",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

UPLOAD_DIR = "./output"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def log_event(level: str, event: str, **fields):
    record = {"event": event, **fields}
    log_line = json.dumps(record, default=str)
    getattr(logger, level, logger.info)(log_line)


def get_db_name_from_file_path(file_path: str) -> str:
    """Infer database name from MessyDesk file path, fallback to env/default."""
    path_parts = file_path.replace('\\\\', '/').split('/')
    if len(path_parts) >= 2 and path_parts[0] == 'data' and path_parts[1]:
        return path_parts[1]
    return os.getenv("DB_NAME", "messydesk")


def get_project_rid(file_node: dict, zip_file: str) -> Optional[str]:
    """Prefer message project_rid; fallback to parsing from file path."""
    rid = file_node.get('project_rid')
    if isinstance(rid, str) and rid:
        return rid

    normalized = zip_file.replace('\\\\', '/')
    marker = '/projects/'
    if marker not in normalized:
        return None

    try:
        segment = normalized.split(marker, 1)[1].split('/', 1)[0]
    except Exception:
        return None

    if not segment:
        return None

    parsed = segment.replace('_', ':')
    if not parsed.startswith('#'):
        parsed = '#' + parsed
    return parsed


def resolve_md_relative_path(relative_path: str) -> str:
    """Resolve a MessyDesk relative path under MD_PATH and block traversal/absolute input."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise HTTPException(400, "Invalid file.path")
    if os.path.isabs(relative_path):
        raise HTTPException(400, "file.path must be relative to MD_PATH")

    md_root = os.path.abspath(MD_ROOT)
    resolved = os.path.abspath(os.path.join(md_root, relative_path))
    if resolved != md_root and not resolved.startswith(md_root + os.sep):
        raise HTTPException(400, "file.path is outside MD_PATH")
    return resolved


def normalize_extensions(values) -> tuple:
    normalized = []
    for item in values:
        if not item:
            continue
        ext = str(item).lower().strip()
        if ext.startswith('.'):
            ext = ext[1:]
        if ext:
            normalized.append(ext)
    return tuple(dict.fromkeys(normalized))


def get_allowed_extensions(task: dict, task_id: Optional[str] = None) -> Optional[tuple]:
    """Resolve allowed extensions.

    For unzip, default is no filtering (extract all files) unless task/env sets
    allowed extensions explicitly.
    """
    params = task.get('params', {}) if isinstance(task, dict) else {}
    from_task = params.get('allowed_extensions')
    if isinstance(from_task, list):
        parsed = normalize_extensions(from_task)
        if parsed:
            return parsed
    if isinstance(from_task, str):
        parsed = normalize_extensions(from_task.split(','))
        if parsed:
            return parsed

    from_env = os.getenv('ZIP_ALLOWED_EXTENSIONS')
    if from_env:
        parsed = normalize_extensions(from_env.split(','))
        if parsed:
            return parsed

    # Unzip should not silently drop files by extension when no filter is set.
    if task_id == 'unzip':
        return None

    return DEFAULT_ALLOWED_EXTENSIONS


def get_db_name_from_abs_path(file_path: str) -> Optional[str]:
    normalized = file_path.replace('\\', '/')
    parts = [part for part in normalized.split('/') if part]
    for idx, part in enumerate(parts):
        if part == 'data' and idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def resolve_any_md_path(file_path: str) -> str:
    """Resolve absolute or MD-relative paths under MD_PATH and block traversal."""
    if not isinstance(file_path, str) or not file_path.strip():
        raise HTTPException(400, "Invalid file path in set_files")

    md_root = os.path.abspath(MD_ROOT)
    if os.path.isabs(file_path):
        resolved = os.path.abspath(file_path)
        if resolved != md_root and not resolved.startswith(md_root + os.sep):
            raise HTTPException(400, "Absolute file path is outside MD_PATH")
        return resolved

    return resolve_md_relative_path(file_path)


def sanitize_zip_filename(name: Optional[str], fallback_prefix: str = "set") -> str:
    candidate = os.path.basename(name) if isinstance(name, str) and name.strip() else ""
    if not candidate:
        candidate = f"{fallback_prefix}_{uuid.uuid4().hex}.zip"
    if not candidate.lower().endswith('.zip'):
        candidate = candidate + '.zip'
    return candidate


def infer_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    if ext in {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'}:
        return 'image'
    if ext == 'json':
        return 'json'
    if ext == 'csv':
        return 'csv'
    if ext == 'pdf':
        return 'pdf'
    if ext == 'zip':
        return 'zip'
    return 'text'


def to_disk_response(task_id: str, files: List[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "task": task_id,
        "response": {
            "type": "disk",
            "files": files,
        },
    }
    payload.update(extra)
    return payload


def create_set_zip_in_tmp(request_json: dict) -> dict:
    set_files = request_json.get('set_files')
    if not isinstance(set_files, list) or len(set_files) == 0:
        raise HTTPException(400, "Missing required field: set_files")

    set_rid = request_json.get('set_rid')
    if not set_rid and isinstance(request_json.get('file'), dict):
        set_rid = request_json['file'].get('@rid')

    db_name = request_json.get('db_name')
    if not db_name:
        for item in set_files:
            if isinstance(item, dict) and isinstance(item.get('path'), str):
                maybe_db = get_db_name_from_file_path(item['path']) if not os.path.isabs(item['path']) else get_db_name_from_abs_path(item['path'])
                if maybe_db:
                    db_name = maybe_db
                    break
    if not db_name:
        db_name = os.getenv("DB_NAME", "messydesk")

    tmp_root = os.path.join(MD_ROOT, "data", db_name, "tmp")
    os.makedirs(tmp_root, exist_ok=True)

    output_name = sanitize_zip_filename(request_json.get('zip_output_name'), "set")
    final_zip_path = os.path.join(tmp_root, output_name)
    partial_zip_path = final_zip_path + ".part"

    if os.path.exists(partial_zip_path):
        os.remove(partial_zip_path)

    zipped_files = 0
    skipped_files = 0
    file_names = []

    try:
        with zipfile.ZipFile(partial_zip_path, 'w', compression=zipfile.ZIP_STORED) as archive:
            for entry in set_files:
                if not isinstance(entry, dict):
                    skipped_files += 1
                    continue

                file_path = entry.get('path')
                if not isinstance(file_path, str):
                    skipped_files += 1
                    continue

                try:
                    abs_path = resolve_any_md_path(file_path)
                except HTTPException:
                    skipped_files += 1
                    continue

                if not os.path.exists(abs_path):
                    skipped_files += 1
                    continue

                arc_name = entry.get('original_filename') or entry.get('label') or os.path.basename(abs_path)
                archive.write(abs_path, arcname=arc_name)
                file_names.append(arc_name)
                zipped_files += 1

            if zipped_files == 0:
                raise HTTPException(404, "No valid files found to zip")

            readme = [
                "MessyDesk set output",
                f"Created on: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
                f"Set ID: {set_rid or 'unknown'}",
                f"Files included: {zipped_files}",
                "",
                "File list:",
                *file_names,
            ]
            archive.writestr('README.txt', "\n".join(readme) + "\n")

        # Atomic rename marks zip as ready for backend downloader.
        os.replace(partial_zip_path, final_zip_path)
    finally:
        if os.path.exists(partial_zip_path):
            os.remove(partial_zip_path)

    return {
        "status": "success",
        "zip_output_name": output_name,
        "zip_abs_path": final_zip_path,
        "zip_tmp_path": f"data/{db_name}/tmp/{output_name}",
        "zipped_files": zipped_files,
        "skipped_files": skipped_files,
    }


@app.get("/")
async def root():
    return {"message": "zip API for MessyDesk"}


@app.post("/process")
async def process_files(
    message: UploadFile = File(...)
):
    process_rid = None
    source_file_rid = None
    output_set = None
    extracted_count = 0
    output_files: List[Dict[str, Any]] = []
    status = "failed"
    start_time = time.time()
    print("Received /process request, starting processing...")

    try:
        log_event("info", "process_start")

        # Parse message JSON in-memory to avoid disk roundtrip overhead.
        request_chunks = []
        while True:
            chunk = await message.read(REQUEST_READ_CHUNK_SIZE)
            if not chunk:
                break
            request_chunks.append(chunk)
        request_json = json.loads(b"".join(request_chunks).decode("utf-8"))
        print(f"Received request: {json.dumps(request_json, indent=2)}")

        log_event("info", "request_parsed", has_payload=isinstance(request_json, dict))

        # Validate
        if not isinstance(request_json, dict):
            raise HTTPException(400, "Request payload must be a JSON object")

        task = request_json.get('task', {})
        task_id = task.get('id') if isinstance(task, dict) else None
        if not task_id:
            raise HTTPException(400, "Missing required fields: task.id")

        # Set zip export task writes archive to MessyDesk tmp and returns file descriptor.
        if task_id == 'zip':
            process_obj = request_json.get('process', {})
            if isinstance(process_obj, dict):
                process_rid = process_obj.get('@rid')
            source_file = request_json.get('file', {})
            if isinstance(source_file, dict):
                source_file_rid = source_file.get('@rid')
            output_set = request_json.get('set_rid') or request_json.get('output_set')

            result = create_set_zip_in_tmp(request_json)
            end_time = time.time()
            status = "success"
            archive_label = result.get("zip_output_name") or os.path.basename(result["zip_abs_path"])
            archive_ext = os.path.splitext(archive_label)[1].lower().lstrip('.') or 'zip'
            output_files = [
                {
                    "path": archive_label,
                    "label": archive_label,
                    "type": "zip",
                    "extension": archive_ext,
                }
            ]
            log_event(
                "info",
                "process_summary",
                status=status,
                process_rid=process_rid,
                source_file_rid=source_file_rid,
                output_set=output_set,
                total_files=result.get('zipped_files', 0),
                successful_uploads=1,
                failed_uploads=result.get('skipped_files', 0),
                duration_sec=round(end_time - start_time, 3),
                zip_output_name=result.get('zip_output_name'),
            )
            return to_disk_response(
                task_id,
                output_files,
                execution_time=round(end_time - start_time, 1),
                status=status,
                zipped_files=result.get("zipped_files", 0),
                skipped_files=result.get("skipped_files", 0),
            )

        if 'file' not in request_json or 'path' not in request_json['file']:
            raise HTTPException(400, "Missing required fields: file.path")

        file_node = request_json.get('file')
        if not isinstance(file_node, dict):
            raise HTTPException(400, "Invalid file object")
        zip_file = file_node.get('path')
        source_file_rid = file_node.get('@rid')
        if not isinstance(zip_file, str):
            raise HTTPException(400, "Invalid file.path")
        zip_path = resolve_md_relative_path(zip_file)
        if not os.path.exists(zip_path):
            raise HTTPException(404, "Zip file not found")
        db_name = get_db_name_from_file_path(zip_file)
        tmp_root = os.path.join(MD_ROOT, "data", db_name, "tmp")
        os.makedirs(tmp_root, exist_ok=True)
        process_obj = request_json.get('process', {})
        if isinstance(process_obj, dict):
            process_rid = process_obj.get('@rid')
        output_set = request_json.get('output_set')
        log_event(
            "info",
            "process_context",
            process_rid=process_rid,
            source_file_rid=source_file_rid,
            output_set=output_set,
            zip_path=zip_path,
            tmp_root=tmp_root,
        )

        project_rid = get_project_rid(file_node, zip_file)
        if not project_rid:
            raise HTTPException(400, "Could not determine project_rid from message or file path")

        task = request_json.get('task')
        if not isinstance(task, dict):
            raise HTTPException(400, "Invalid task object")
        allowed_extensions = get_allowed_extensions(task, task_id)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                allowed_files = []
                for item in zip_ref.infolist():
                    if item.is_dir():
                        continue
                    base_name = os.path.basename(item.filename)
                    if not base_name:
                        continue
                    if allowed_extensions is None:
                        allowed_files.append(item)
                    else:
                        ext = os.path.splitext(base_name)[1].lower().lstrip('.')
                        if ext in allowed_extensions:
                            allowed_files.append(item)

                total_allowed = len(allowed_files)
                for _, file in enumerate(allowed_files, start=1):
                    extracted_count += 1
                    safe_name = os.path.basename(file.filename)
                    tmp_filename = f"zipfs_{uuid.uuid4().hex}_{safe_name}"
                    dest_path = os.path.join(tmp_root, tmp_filename)

                    # Stream extract directly to data/<db>/tmp.
                    with zip_ref.open(file, 'r') as src, open(dest_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst, length=COPY_CHUNK_SIZE)

                    ext = os.path.splitext(safe_name)[1].lower().lstrip('.')
                    output_files.append(
                        {
                            "path": tmp_filename,
                            "label": safe_name,
                            "type": infer_file_type(safe_name),
                            "extension": ext or "bin",
                        }
                    )

                if total_allowed == 0:
                    raise HTTPException(404, "No files matching allowed extensions found in zip")

        except zipfile.BadZipFile:
            raise HTTPException(400, "Invalid or corrupted zip file")
        except HTTPException:
            raise
        except Exception as e:
            log_event("error", "zip_processing_error", process_rid=process_rid, error=str(e))
            raise HTTPException(500, f"Error processing zip file: {str(e)}")

        end_time = time.time()
        status = "success"
        log_event(
            "info",
            "process_summary",
            status=status,
            process_rid=process_rid,
            source_file_rid=source_file_rid,
            output_set=output_set,
            total_files=extracted_count,
            successful_uploads=len(output_files),
            failed_uploads=0,
            duration_sec=round(end_time - start_time, 3),
        )
        return to_disk_response(
            task_id,
            output_files,
            execution_time=round(end_time - start_time, 1),
            total_files=extracted_count,
            current_file=extracted_count,
            status=status,
        )

    except HTTPException:
        end_time = time.time()
        if status == "failed":
            log_event(
                "warning",
                "process_summary",
                status="failed",
                process_rid=process_rid,
                source_file_rid=source_file_rid,
                output_set=output_set,
                total_files=extracted_count,
                successful_uploads=len(output_files),
                failed_uploads=0,
                duration_sec=round(end_time - start_time, 3),
            )
        raise
    except Exception as e:
        log_event("error", "process_unhandled_exception", process_rid=process_rid, error=str(e))
        raise HTTPException(500, f"Processing failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    log_event(
        "info",
        "service_start",
        md_url=MD_URL,
        md_path_env=MD_PATH_ENV,
        container_mode=CONTAINER_MODE,
        md_root=MD_ROOT,
    )
    uvicorn.run(app, host="0.0.0.0", port=9004)
