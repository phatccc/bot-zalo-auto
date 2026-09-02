"""Small, dependency-free progress page for the Zalo importer."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ProgressTracker:
    """Keeps only operational batch status; never stores credentials or image URLs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._batches: list[dict[str, Any]] = []

    def start(self, total: int, sender_name: str, main_acc: str) -> str:
        batch_id = f"{int(time.time() * 1000)}-{len(self._batches)}"
        batch = {
            "id": batch_id,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sender_name": sender_name,
            "main_acc": main_acc,
            "total": total,
            "success": 0,
            "failed": 0,
            "stage": "Đang chuẩn bị",
            "done": False,
            "items": [{"position": position, "stage": "Chờ xử lý", "detail": ""} for position in range(1, total + 1)],
        }
        with self._lock:
            self._batches.insert(0, batch)
            del self._batches[12:]
        return batch_id

    def update_batch(self, batch_id: str, stage: str, *, success: int | None = None, failed: int | None = None, done: bool | None = None) -> None:
        with self._lock:
            batch = self._find(batch_id)
            if not batch:
                return
            batch["stage"] = stage
            if success is not None:
                batch["success"] = success
            if failed is not None:
                batch["failed"] = failed
            if done is not None:
                batch["done"] = done

    def update_item(self, batch_id: str, position: int, stage: str, detail: str = "") -> None:
        with self._lock:
            batch = self._find(batch_id)
            if not batch or position < 1 or position > len(batch["items"]):
                return
            item = batch["items"][position - 1]
            item["stage"] = stage
            item["detail"] = detail[:180]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            # JSON round trip is an easy way to return an isolated primitive copy.
            return json.loads(json.dumps({"batches": self._batches}, ensure_ascii=False))

    def _find(self, batch_id: str) -> dict[str, Any] | None:
        return next((batch for batch in self._batches if batch["id"] == batch_id), None)


PAGE = """<!doctype html>
<html lang=\"vi\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Zalo bot – tiến độ</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#e7edf8;font:15px system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:900px;margin:auto;padding:28px 16px}h1{margin:0 0 6px;font-size:25px}.sub{color:#94a3b8;margin:0 0 24px}.card{background:#151d31;border:1px solid #263451;border-radius:14px;padding:18px;margin:13px 0}.head{display:flex;justify-content:space-between;gap:12px;align-items:start}.name{font-weight:700;font-size:17px}.muted{color:#a9b7cd;font-size:13px}.bar{height:10px;border-radius:99px;background:#293652;overflow:hidden;margin:15px 0 8px}.fill{height:100%;background:linear-gradient(90deg,#38bdf8,#34d399);transition:width .3s}.items{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin-top:14px}.item{padding:9px;border-radius:9px;background:#0e1628;border:1px solid #263451}.ok{color:#5eead4}.fail{color:#fda4af}.active{color:#7dd3fc}.empty{color:#94a3b8;text-align:center;padding:36px}
</style><body><main class=\"wrap\"><h1>Tiến độ Zalo Bot</h1><p class=\"sub\">Tự làm mới mỗi 0,8 giây · không hiển thị cookie, IMEI hoặc link ảnh.</p><div id=\"app\" class=\"empty\">Đang tải…</div></main>
<script>
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
function cls(stage){return /hoàn tất|lưu web|đã cập nhật/i.test(stage)?'ok':/lỗi|thất bại|bỏ/i.test(stage)?'fail':/chờ/i.test(stage)?'muted':'active'}
function render(data){let batches=data.batches||[];app.innerHTML=batches.length?batches.map(b=>{let complete=b.success+b.failed,p=Math.round(100*complete/b.total);return `<section class=\"card\"><div class=\"head\"><div><div class=\"name\">${esc(b.main_acc)}</div><div class=\"muted\">Người gửi: ${esc(b.sender_name)} · ${esc(b.started_at)}</div></div><div class=\"${b.done?'ok':'active'}\">${esc(b.stage)}</div></div><div class=\"bar\"><div class=\"fill\" style=\"width:${p}%\"></div></div><div>${complete}/${b.total} ảnh · <span class=\"ok\">${b.success} thành công</span> · <span class=\"fail\">${b.failed} lỗi</span></div><div class=\"items\">${b.items.map(x=>`<div class=\"item\"><b>Ảnh ${x.position}</b><br><span class=\"${cls(x.stage)}\">${esc(x.stage)}</span>${x.detail?`<br><small class=\"muted\">${esc(x.detail)}</small>`:''}</div>`).join('')}</div></section>`}).join(''):'<div class=\"empty\">Chưa có batch nào đang hoặc đã xử lý.</div>'}
async function load(){try{render(await (await fetch('/api/progress',{cache:'no-store'})).json())}catch(e){app.innerHTML='<div class=\"empty\">Không đọc được tiến độ. Bot có thể vừa khởi động lại.</div>'}}load();setInterval(load,800);
</script></body></html>"""


def start_dashboard(tracker: ProgressTracker, settings: dict[str, Any]) -> str | None:
    config = settings.get("dashboard") or {}
    if not config.get("enabled", True):
        return None
    host = str(config.get("host") or "0.0.0.0")
    try:
        port = int(config.get("port") or 8787)
    except (TypeError, ValueError):
        port = 8787

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - required HTTP handler name
            if self.path.split("?", 1)[0] == "/api/progress":
                content = json.dumps(tracker.snapshot(), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            elif self.path.split("?", 1)[0] in ("/", "/index.html"):
                content = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            else:
                self.send_error(404)
                return
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format, *_args):
            return

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as error:
        print(f"[DASHBOARD] Không khởi động được cổng {host}:{port}: {error}", flush=True)
        return None
    threading.Thread(target=server.serve_forever, daemon=True, name="progress-dashboard").start()
    if host in {"0.0.0.0", "::"}:
        return f"http://<IP-VPS-CUA-BAN>:{port}"
    return f"http://{host}:{port}"
