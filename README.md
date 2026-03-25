
# MD-zip_fs

An experimental MessyDesk wrapper for zip

The purposes of this script is to provide an endpoint to MessyDesk for zip extracting.
This reads zip file directly from project directory of MessyDesk (_fs = file storage).

The service writes extracted files directly to `data/<DB_NAME>/tmp` and reports them through
`POST /api/nomad/process/files/tmp`.

## API

endpoint is `http://localhost:9004/process`

Payload is queue message as multipart file field `request` containing JSON.

The service extracts matching files from a zip under MessyDesk storage and stages them
to `data/<DB_NAME>/tmp`, then calls:

- `POST /api/nomad/process/files/tmp`

It also supports an internal `zip` task for set downloads. In that mode it:

- receives set file list through queue message payload,
- writes resulting archive to `data/<DB_NAME>/tmp/<zip_output_name>`,
- does not call backend callback endpoints.

## Running as service (locally)

Create .env file with MD_PATH like this:

	MD_PATH="/home/YOUR_USERNAME/Projects/MessyDesk"
	MD_URL="http://localhost:8200"

### Run with python

	python api.py


### Run with docker/podman

Build and start

	make build
	make start

or directly

	docker run --name md-zip_fs -p 9004:9004 -e MD_URL=http://host.containers.internal:8200 -v [MESSYDESK-PATH]/data/:/app/data:Z zip-api

## Adapter (MD-consumer)

	TOPIC=md-zip_fs node src/index.mjs



### Example API call 

Run these from MD-zip_fs directory:



	curl -X POST -H "Content-Type: multipart/form-data" \
	  -F "request=@test/extract.json;type=application/json" \
	  http://localhost:9004/process


## Config

Required:

- `MD_PATH`: path to the MessyDesk root (directory that contains `data/`)
	- local Python run (host): `MD_PATH=/home/<user>/Projects/MessyDesk`
	- container run: `MD_PATH=/app` (because host `.../MessyDesk/data` is mounted to `/app/data`)
- `CONTAINER`: set `CONTAINER=true` when running in container

Path notes:

- Service resolves runtime root from `MD_PATH` and validates it contains `data/`.
- Container mode is explicit; no container auto-detection heuristics are used.

Optional:

- `MD_URL` (default `http://localhost:8200`)
- `DB_NAME` fallback when DB name cannot be inferred from `file.path`
- `ZIP_ALLOWED_EXTENSIONS` (comma-separated, e.g. `txt,jpg,jpeg,png,pdf`)
- `REQUEST_READ_CHUNK_SIZE` (bytes)
- `COPY_CHUNK_SIZE` (bytes)
- `MD_CALLBACK_WORKERS`
- `MD_CALLBACK_CONNECT_TIMEOUT`
- `MD_CALLBACK_READ_TIMEOUT`
- `MD_CALLBACK_RETRIES`
- `MD_CALLBACK_RETRY_BACKOFF_SEC`
- `LOG_LEVEL` (default `INFO`)

## Testing

The service can be tested without a running MessyDesk backend.
Tests mock outbound `requests.post` calls and use a temporary local `MD_PATH`.

Run:

	python -m unittest -v test/test_api.py





