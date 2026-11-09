"""Runway / Kling clients against a local fake API: checks the request flow
(upload → task → polling → download) without any network call or spend."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from decorpipe.decors import load_decor  # noqa: E402


@pytest.fixture()
def fake_api(tmp_path):
    clip = tmp_path / "ai_result.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=720x1280:d=1:r=25",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(clip)], check=True)
    seen: list[tuple[str, str, dict]] = []
    polls = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            ctype = self.headers.get("Content-Type", "")
            payload = json.loads(raw) if ctype.startswith("application/json") else {}
            seen.append(("POST", self.path, payload | {"_headers": dict(self.headers)}))
            base = f"http://127.0.0.1:{self.server.server_port}"
            if self.path == "/v1/uploads":
                return self._json({"uploadUrl": f"{base}/s3", "fields": {"key": "k"},
                                   "runwayUri": "runway://upload/123"})
            if self.path == "/s3":
                return self._json({})
            if self.path == "/v1/video_to_video":
                return self._json({"id": "task-1"})
            if self.path.startswith("/fal-ai/"):
                return self._json({"request_id": "req-1", "status_url": f"{base}/status",
                                   "response_url": f"{base}/response"})
            self._json({"error": "unknown"}, 404)

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_port}"
            seen.append(("GET", self.path, {}))
            if self.path == "/v1/tasks/task-1" or self.path == "/status":
                polls["n"] += 1
                done = polls["n"] % 2 == 0
                if self.path == "/status":
                    return self._json({"status": "COMPLETED" if done else "IN_PROGRESS"})
                return self._json({"status": "SUCCEEDED" if done else "RUNNING",
                                   "output": [f"{base}/result.mp4"]})
            if self.path == "/response":
                return self._json({"video": {"url": f"{base}/result.mp4"}})
            if self.path == "/result.mp4":
                data = clip.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._json({"error": "unknown"}, 404)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    cfg = json.loads((ROOT / "config" / "engines.json").read_text())
    cfg["runway"]["base_url"] = f"{base}/v1"
    cfg["kling"]["queue_url"] = base
    cfg["budget"]["ledger"] = str(tmp_path / "depenses.csv")
    cfg_path = tmp_path / "engines.json"
    cfg_path.write_text(json.dumps(cfg))
    env = {"DECORPIPE_ENGINES": str(cfg_path), "RUNWAYML_API_SECRET": "test-key", "FAL_KEY": "test-key",
           "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    import decorpipe.engines.ai_common as ac
    ac_poll = ac.poll
    ac.poll = lambda fn, **kw: ac_poll(fn, interval=0.01, **kw)
    master = tmp_path / "master.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=1080x1920:d=1:r=25",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(master)], check=True)
    yield seen, master, tmp_path
    ac.poll = ac_poll
    srv.shutdown()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_runway_flow(fake_api):
    from decorpipe.engines import runway
    seen, master, tmp = fake_api
    out, task = runway.runway_edit(master, tmp, load_decor("haussmannien"), log=lambda *a: None)
    assert task == "task-1" and out.exists() and out.stat().st_size > 1000
    create = next(p for m, path, p in seen if path == "/v1/video_to_video")
    assert create["model"] == "aleph2" and create["videoUri"] == "runway://upload/123"
    assert "Haussmann" in create["promptText"] and create["ratio"] == "720:1280"
    assert create["_headers"]["Authorization"] == "Bearer test-key"
    assert create["_headers"]["X-Runway-Version"]


def test_kling_flow(fake_api):
    from decorpipe.engines import kling
    seen, master, tmp = fake_api
    out, req = kling.kling_edit(master, tmp, load_decor("hotel-luxe"), log=lambda *a: None)
    assert req == "req-1" and out.exists()
    sub = next(p for m, path, p in seen if path.startswith("/fal-ai/kling-video"))
    assert sub["video_url"].startswith("data:video/mp4;base64,")
    assert sub["keep_audio"] is False and sub["image_urls"][0].startswith("data:image/jpeg")
    assert sub["_headers"]["Authorization"] == "Key test-key"
