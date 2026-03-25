import io
import json
import os
import sys
import importlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class ZipFsApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.repo_root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(cls.repo_root))

        os.environ["MD_PATH"] = cls.tempdir.name
        os.environ["MD_URL"] = "http://localhost:8200"

        if "api" in sys.modules:
            del sys.modules["api"]
        cls.api = importlib.import_module("api")

        # Keep callback execution deterministic in tests.
        setattr(cls.api, "CALLBACK_WORKERS", 1)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def _create_zip_in_md_path(self, rel_zip_path, entries):
        abs_zip_path = Path(self.tempdir.name) / rel_zip_path
        abs_zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(abs_zip_path, "w") as zf:
            for name, content in entries.items():
                zf.writestr(name, content)
        return abs_zip_path

    def _build_message(self, rel_zip_path):
        return {
            "service": {"id": "md-zip_fs"},
            "task": {"id": "unzip", "params": {}},
            "file": {
                "@rid": "#79:18",
                "project_rid": "#1:4",
                "path": rel_zip_path,
                "type": "zip",
            },
            "process": {"@rid": "#106:13"},
            "output_set": "#127:5",
            "userId": "#49:0",
        }

    async def _call_process(self, payload):
        content = json.dumps(payload).encode("utf-8")
        upload = UploadFile(filename="request.json", file=io.BytesIO(content))
        return await self.api.process_files(upload)

    async def test_process_success_without_backend(self):
        rel_zip_path = "data/dir_test/projects/1_4/files/a/b/c/source/source.zip"
        self._create_zip_in_md_path(
            rel_zip_path,
            {
                "images/hamburger.jpg": b"img-bytes",
                "docs/readme.txt": b"hello",
                "skip.bin": b"ignored",
            },
        )

        message = self._build_message(rel_zip_path)
        with patch("api.requests.post", return_value=FakeResponse(200, {"success": True})) as mocked_post:
            result = await self._call_process(message)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_files"], 2)
        self.assertEqual(result["successful_uploads"], 2)
        self.assertEqual(result["failed_uploads"], 0)
        self.assertEqual(mocked_post.call_count, 2)

    async def test_process_partial_success_on_callback_failure(self):
        rel_zip_path = "data/dir_test/projects/1_4/files/a/b/c/source/single.zip"
        self._create_zip_in_md_path(
            rel_zip_path,
            {
                "images/hamburger.jpg": b"img-bytes",
            },
        )

        message = self._build_message(rel_zip_path)
        with patch("api.requests.post", return_value=FakeResponse(500, text="backend error")):
            result = await self._call_process(message)

        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["total_files"], 1)
        self.assertEqual(result["successful_uploads"], 0)
        self.assertEqual(result["failed_uploads"], 1)
        self.assertEqual(len(result["errors"]), 1)

    async def test_rejects_path_traversal_without_backend(self):
        message = self._build_message("../../etc/passwd")

        with self.assertRaises(HTTPException) as ctx:
            await self._call_process(message)

        self.assertEqual(ctx.exception.status_code, 400)

    async def test_retries_callback_then_succeeds(self):
        rel_zip_path = "data/dir_test/projects/1_4/files/a/b/c/source/retry.zip"
        self._create_zip_in_md_path(
            rel_zip_path,
            {
                "images/hamburger.jpg": b"img-bytes",
            },
        )

        message = self._build_message(rel_zip_path)
        side_effect = [
            FakeResponse(503, text="busy"),
            FakeResponse(200, {"success": True}),
        ]

        with patch.object(self.api, "CALLBACK_RETRIES", 2), \
             patch.object(self.api, "CALLBACK_RETRY_BACKOFF_SEC", 0), \
             patch("api.requests.post", side_effect=side_effect) as mocked_post:
            result = await self._call_process(message)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["successful_uploads"], 1)
        self.assertEqual(result["failed_uploads"], 0)
        self.assertEqual(mocked_post.call_count, 2)

    async def test_invalid_or_broken_zip_returns_400(self):
        rel_zip_path = "data/dir_test/projects/1_4/files/a/b/c/source/broken.zip"
        abs_zip_path = Path(self.tempdir.name) / rel_zip_path
        abs_zip_path.parent.mkdir(parents=True, exist_ok=True)
        abs_zip_path.write_bytes(b"not-a-valid-zip")

        message = self._build_message(rel_zip_path)
        with self.assertRaises(HTTPException) as ctx:
            await self._call_process(message)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid or corrupted zip file", str(ctx.exception.detail))

    async def test_zip_task_creates_archive_in_tmp_without_backend_callback(self):
        data_root = Path(self.tempdir.name) / "data" / "dir_test"
        src_dir = data_root / "projects" / "1_4" / "files" / "set_export"
        src_dir.mkdir(parents=True, exist_ok=True)

        file_a = src_dir / "a.txt"
        file_b = src_dir / "b.jpg"
        file_a.write_text("hello", encoding="utf-8")
        file_b.write_bytes(b"jpg-bytes")

        output_name = "set_export_test.zip"
        message = {
            "task": {"id": "zip", "params": {}},
            "set_rid": "#127:5",
            "db_name": "dir_test",
            "zip_output_name": output_name,
            "set_files": [
                {"path": "data/dir_test/projects/1_4/files/set_export/a.txt", "label": "a.txt"},
                {"path": "data/dir_test/projects/1_4/files/set_export/b.jpg", "label": "b.jpg"},
            ],
            "userId": "#49:0",
        }

        with patch("api.requests.post") as mocked_post:
            result = await self._call_process(message)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["zipped_files"], 2)
        self.assertEqual(result["skipped_files"], 0)
        mocked_post.assert_not_called()

        zip_path = Path(self.tempdir.name) / "data" / "dir_test" / "tmp" / output_name
        self.assertTrue(zip_path.exists())
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            self.assertIn("a.txt", names)
            self.assertIn("b.jpg", names)
            self.assertIn("README.txt", names)


if __name__ == "__main__":
    unittest.main()
