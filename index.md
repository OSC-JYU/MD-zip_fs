# MD-zip_fs Service

Zip file utility service for MessyDesk.

## Endpoints

- `GET /health` is not implemented.
- `GET /config` returns service descriptor JSON from `service.json`.
- `GET /help` returns this markdown help page.
- `POST /process` handles zip tasks.

## Supported tasks

### `unzip`

Extracts files from an input zip file into MessyDesk temporary storage.

Optional task param:

- `allowed_extensions`: comma-separated extension list (for example `pdf,txt,png`).

Output:

- Disk response (`response.type = "disk"`) with extracted file descriptors.

### `zip`

Creates one zip file from `set_files` input and stores it in MessyDesk temporary storage.

Output:

- Disk response with one generated zip file descriptor.

## Environment variables

Required in disk mode:

- `MD_PATH`: MessyDesk root path (directory containing `data/`).

Optional:

- `MD_URL` (default `http://localhost:8200`)
- `CONTAINER` (`true/false`)
- `ZIP_ALLOWED_EXTENSIONS`
- `SERVICE_DESCRIPTOR_PATH` (default `./service.json`)
- `SERVICE_HELP_PATH` (default `./index.md`)
- `SERVICE_HELP_FALLBACK_PATH` (default `./README.md`)
- `SERVICE_ID`, `SERVICE_NAME`, `SERVICE_ADAPTER`, `SERVICE_LOCAL_URL`
