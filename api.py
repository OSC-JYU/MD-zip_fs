from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import uuid
import json
import zipfile
import aiohttp
import asyncio
from typing import List
import requests

from pydantic import BaseModel

MD_URL = os.getenv("MD_URL", "http://localhost:8200")
MD_PATH = os.getenv("MD_PATH", "")

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


def send_file_to_upload_endpoint_sync(file_path: str, project_rid: str, set_rid: str = None) -> dict:
    """
    Send a single file to the upload endpoint using requests (synchronous)
    """
    url = f"{MD_URL}/api/nomad/process/files"
    print("Uploading file to:", url)

    # Determine content type based on file extension
    file_extension = os.path.splitext(file_path)[1].lower()
    content_type = {
        '.txt': 'text/plain',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png'
    }.get(file_extension, 'application/octet-stream')

    message = {
        "file": {
            "path": file_path,
            "type": "image",
            "extension": file_extension
        },
        "target": project_rid,
        "output_set": set_rid
    }

    try:
        with open(file_path, 'rb') as f:
            files = {
                'file': (
                    os.path.basename(file_path),
                    f,
                    content_type
                )
            }
            response = requests.post(url, files=files)
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error uploading {file_path}: {response.status_code}")
                return None
    except Exception as e:
        print(f"Error sending file {file_path}: {str(e)}")
        return None



@app.get("/")
async def root():
    return {"message": "zip API for MessyDesk"}


@app.post("/process")
async def process_files(
    request: UploadFile = File(...)
):
    try:
        print("Processing files...")
   
        # Start execution time counter
        import time
        start_time = time.time()

        # read request as JSON
        request_data = await request.read()
        request_json = json.loads(request_data.decode('utf-8'))
        print("Parsed JSON:", request_json)

        if 'file' not in request_json or 'path' not in request_json['file']:
            raise HTTPException(
                status_code=400,
                detail="Missing required fields: file.path"
            )

        if 'target' not in request_json:
            raise HTTPException(
                status_code=400,
                detail="Missing required field: target"
            )


        set_rid = request_json.get('output_set').replace("#", "")
        print("Set rid:", set_rid)

        file_node = request_json.get('file')
        zip_file = file_node.get('path')
        zip_path = os.path.join(MD_PATH, zip_file)
        print("Zip file path:", zip_path)

        # get project rid from file path
        project_rid = zip_file.split("/projects/")[1].split("/")[0].replace("_", ":")
        print("Project rid:", project_rid)

        # make sure the file exists
        # if not os.path.exists(zip_path):
        #     raise HTTPException(
        #         status_code=404,
        #         detail="File not found"
        #     )

        # Allowed extensions
        allowed_extensions = ('.txt', '.jpg', '.jpeg', '.png')

        extracted_files = []
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Get list of files in zip
                file_list = zip_ref.namelist()
                
                # Extract only files with allowed extensions
                for file in file_list:
                    if file.lower().endswith(allowed_extensions):
                        zip_ref.extract(file, request_json['target'])
                        extracted_files.append(os.path.join(request_json['target'], file))
                        print(f"Extracted: {file}")

        except zipfile.BadZipFile:
            print("Invalid or corrupted zip file")
            raise HTTPException(
                status_code=400,
                detail="Invalid or corrupted zip file"
            )
        except Exception as e:
            print("Error extracting files:", e)
            raise HTTPException(
                status_code=500,
                detail=f"Error processing zip file: {str(e)}"
            )

        print("Extracted files:", extracted_files)
        
        # Send each extracted file to the upload endpoint using synchronous requests
        successful_uploads = []
        for file_path in extracted_files:
            result = send_file_to_upload_endpoint_sync(
                file_path, 
                project_rid,
                set_rid
            )
            if result:
                successful_uploads.append(result)

        # End execution time counter
        end_time = time.time()
        return {
            "execution_time": round(end_time - start_time, 1),
            "total_files": len(extracted_files),
            "successful_uploads": len(successful_uploads)
        }
        
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Processing failed: {str(e)}"
        )



if __name__ == "__main__":
    import uvicorn
    print(MD_URL)
    uvicorn.run(app, host="0.0.0.0", port=9004) 
