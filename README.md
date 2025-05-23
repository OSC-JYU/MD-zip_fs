
# MD-zip_fs

An experimental MessyDesk wrapper for zip

The purposes of this script is to provide an endpoint to MessyDesk for zip extracting. 
This reads zip file directly from project directory of MessyDesk (_fs = file storage)

## API

endpoint is http://localhost:9003/process

Payload is queue message as json file. 

## Running as service (locally)

### Run with python

	MD_PATH="/home/YOUR_USERNAME/MessyDesk" python api.py 


### Run with docker/podman

Create .env file with MD_PATH like this:

	MD_PATH="/home/YOUR_USERNAME/MessyDesk"

Then build and start

	make build
	make start

or directly

 	docker run --name pypdf -p 9004:9004 -v [MESSYDESK-PATH]/data/:/app/data:Z zip-api 

## Adapter (MD-consumer)

	TOPIC=md-zip_fs node src/index.mjs



### Example API call 

Run these from MD-zip_fs directory:



	curl -X POST -H "Content-Type: multipart/form-data" \
	  -F "request=@test/extract.json;type=application/json" \
	  http://localhost:9004/process





