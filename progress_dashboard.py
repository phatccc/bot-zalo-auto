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
<title>Zalo Bot · Tiến độ xử lý</title>
<style>
:root{color-scheme:dark;--bg:#070b16;--surface:#0f172a;--surface-2:#111c33;--line:#233657;--line-soft:#1b2a46;--text:#f1f5f9;--muted:#91a4c2;--blue:#5ebcff;--cyan:#31d6e5;--green:#32d296;--red:#fb7185;--amber:#f6bd45;--radius:18px}*{box-sizing:border-box}body{min-width:320px;margin:0;min-height:100vh;background:radial-gradient(850px 500px at 10% -10%,#183566 0%,transparent 64%),radial-gradient(700px 380px at 95% 0%,#10334e 0%,transparent 66%),var(--bg);color:var(--text);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif}.wrap{width:min(1280px,calc(100% - 32px));margin:auto;padding:32px 0 50px}.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding:4px 0 26px}.brand{display:flex;gap:13px;align-items:center}.mark{display:grid;place-items:center;width:42px;height:42px;border:1px solid #3285df;border-radius:13px;background:linear-gradient(145deg,#1677d2,#22cad7);box-shadow:0 12px 34px #0b4c8e66;color:white;font-weight:900;font-size:18px;letter-spacing:-1px}.eyebrow{margin:0 0 3px;color:#88c5ff;font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.title{margin:0;font-size:24px;line-height:1.2;letter-spacing:-.035em}.subtitle{margin:4px 0 0;color:var(--muted);font-size:13px}.live{display:inline-flex;align-items:center;gap:8px;flex:none;margin-top:6px;padding:8px 11px;border:1px solid #24466a;border-radius:999px;background:#0b2036;color:#bae4ff;font-size:12px;font-weight:700}.dot{width:7px;height:7px;border-radius:99px;background:var(--green);box-shadow:0 0 0 4px #32d29622;animation:pulse 2s ease-in-out infinite}.summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:16px}.summary-card{min-height:82px;padding:15px 16px;border:1px solid var(--line-soft);border-radius:var(--radius);background:linear-gradient(135deg,#101a30dd,#0d1527dd);box-shadow:0 16px 35px #00000024}.summary-label{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px;font-weight:650}.summary-value{margin-top:3px;font-size:23px;font-weight:800;letter-spacing:-.04em}.summary-sub{color:var(--muted);font-size:11px}.toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:22px 0 12px}.toolbar h2{margin:0;font-size:14px;letter-spacing:.02em}.filters{display:flex;gap:6px;padding:4px;border:1px solid var(--line-soft);border-radius:11px;background:#0a1222}.filter{border:0;border-radius:7px;padding:6px 10px;background:transparent;color:var(--muted);font:inherit;font-size:12px;font-weight:700;cursor:pointer}.filter:hover{color:var(--text)}.filter.selected{background:#1d3457;color:#dff2ff;box-shadow:inset 0 0 0 1px #37628e}.sync{color:#7d92b3;font-size:11px}.batches{display:grid;gap:12px}.batch{overflow:hidden;border:1px solid var(--line);border-radius:var(--radius);background:linear-gradient(145deg,#111c31f7,#0d1527f7);box-shadow:0 16px 45px #00000026}.batch.is-done{border-color:#1d3f54}.batch-head{display:flex;align-items:center;gap:12px;padding:16px 17px 14px}.batch-icon{display:grid;place-items:center;flex:none;width:38px;height:38px;border-radius:12px;background:#162b49;border:1px solid #28517d;color:#89d1ff;font-size:16px}.batch.is-done .batch-icon{background:#10372e;border-color:#236c55;color:#7ff2c5}.batch-copy{min-width:0;flex:1}.batch-title-row{display:flex;align-items:center;gap:8px;min-width:0}.batch-title{overflow:hidden;color:#f5f9ff;font-weight:800;font-size:15px;white-space:nowrap;text-overflow:ellipsis}.state{display:inline-flex;align-items:center;gap:5px;flex:none;border:1px solid #1f526d;border-radius:99px;padding:3px 7px;background:#0d273d;color:#8bd9ff;font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.state.done{border-color:#21674f;background:#0d3027;color:#71ebbb}.batch-meta{overflow:hidden;margin-top:2px;color:var(--muted);font-size:12px;white-space:nowrap;text-overflow:ellipsis}.toggle{flex:none;border:1px solid #2b405f;border-radius:9px;padding:7px 10px;background:#111e33;color:#bfcee4;font:inherit;font-size:12px;font-weight:700;cursor:pointer}.toggle:hover{border-color:#4776a5;color:white}.batch-body{padding:0 17px 16px}.progress-top{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:8px}.progress-label{font-size:12px;font-weight:800}.progress-numbers{color:var(--muted);font-size:12px}.progress-numbers strong{color:var(--text);font-size:14px}.bar{height:8px;overflow:hidden;border-radius:99px;background:#1c2b45;box-shadow:inset 0 1px 2px #0005}.fill{height:100%;min-width:0;border-radius:inherit;background:linear-gradient(90deg,#2a9ff2,#2ad9c4);box-shadow:0 0 16px #2ad9c477;transition:width .35s ease}.batch.is-done .fill{background:linear-gradient(90deg,#22b883,#55e8b2)}.result-row{display:flex;flex-wrap:wrap;gap:7px;margin-top:11px}.metric{display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:7px;background:#101d32;color:#a9bbd3;font-size:11px;font-weight:700}.metric.ok{background:#0d3027;color:#74ebbd}.metric.fail{background:#371622;color:#ffadb9}.items-panel{display:none;margin-top:14px;padding-top:14px;border-top:1px solid var(--line-soft)}.batch.expanded .items-panel{display:block}.items{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:8px}.item{display:flex;gap:9px;align-items:flex-start;min-width:0;padding:10px;border:1px solid #223552;border-radius:11px;background:#0b1425}.item-icon{display:grid;place-items:center;flex:none;width:22px;height:22px;border-radius:7px;background:#1b2b45;color:#91a6c4;font-size:11px;font-weight:900}.item.ok .item-icon{background:#123c30;color:#70f0bb}.item.fail .item-icon{background:#46202a;color:#ffb4bf}.item.active .item-icon{background:#12395a;color:#81d7ff}.item-copy{min-width:0}.item-name{font-size:12px;font-weight:800}.item-stage{overflow:hidden;margin-top:1px;color:var(--muted);font-size:11px;white-space:nowrap;text-overflow:ellipsis}.item.ok .item-stage{color:#66deb0}.item.fail .item-stage{color:#ff9aa9}.item.active .item-stage{color:#7ed6ff}.item-detail{overflow:hidden;margin-top:2px;color:#8c9db6;font-size:10px;white-space:nowrap;text-overflow:ellipsis}.empty{padding:60px 18px;border:1px dashed #2b4265;border-radius:var(--radius);background:#0d1628a8;color:var(--muted);text-align:center}.empty-icon{display:grid;place-items:center;width:42px;height:42px;margin:0 auto 12px;border-radius:13px;background:#142542;color:#8ecbff;font-size:20px}.empty strong{display:block;color:#d9e5f7;font-size:14px}.empty p{margin:4px 0 0;font-size:12px}@keyframes pulse{50%{opacity:.45;transform:scale(.82)}}@media(max-width:640px){.wrap{width:min(100% - 22px,1280px);padding-top:19px}.topbar{align-items:flex-start;padding-bottom:19px}.subtitle{max-width:240px}.live{padding:7px 9px;font-size:0}.live .dot{margin:1px}.summary{grid-template-columns:repeat(3,1fr);gap:7px}.summary-card{min-height:72px;padding:11px 10px}.summary-label{font-size:10px}.summary-value{font-size:19px}.summary-sub{display:none}.toolbar{margin-top:17px}.sync{display:none}.batch-head{padding:13px;gap:9px}.batch-icon{width:33px;height:33px;border-radius:10px}.batch-title-row{gap:5px}.batch-title{font-size:13px}.state{padding:3px 5px;font-size:9px}.batch-meta{font-size:10px}.toggle{padding:6px 7px;font-size:11px}.batch-body{padding:0 13px 13px}.items{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.item{padding:8px;gap:6px}.item-name{font-size:11px}.item-stage{font-size:10px}.result-row{gap:5px}.metric{font-size:10px}.filter{padding:6px 8px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style><body><main class=\"wrap\"><header class=\"topbar\"><div class=\"brand\"><div class=\"mark\">Z</div><div><p class=\"eyebrow\">Zalo batch importer</p><h1 class=\"title\">Tiến độ xử lý</h1><p class=\"subtitle\">Chỉ hiển thị trạng thái vận hành, không hiển thị cookie, IMEI hoặc liên kết ảnh.</p></div></div><div class=\"live\"><i class=\"dot\"></i><span>KẾT NỐI</span></div></header><section class=\"summary\" id=\"summary\" aria-label=\"Tổng quan tiến độ\"></section><div class=\"toolbar\"><h2>Danh sách batch</h2><div class=\"filters\" aria-label=\"Lọc batch\"><button class=\"filter selected\" data-filter=\"active\">Đang chạy</button><button class=\"filter\" data-filter=\"all\">Tất cả</button><button class=\"filter\" data-filter=\"done\">Đã xong</button></div><span class=\"sync\" id=\"sync\">Đang đồng bộ…</span></div><div id=\"app\" class=\"batches\" aria-live=\"polite\"><div class=\"empty\">Đang tải…</div></div></main>
<script>
const app=document.getElementById('app'),summary=document.getElementById('summary'),sync=document.getElementById('sync');let filter='active';const openBatches=new Set();
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
function status(stage){const s=String(stage||'');return /^(hoàn tất|đã cập nhật)\b/i.test(s)?'ok':/lỗi|thất bại|bỏ/i.test(s)?'fail':/chờ/i.test(s)?'wait':'active'}
function icon(kind){return kind==='ok'?'✓':kind==='fail'?'!':kind==='active'?'↗':'·'}
function count(items,kind){return items.filter(x=>status(x.stage)===kind).length}
function formatTime(){return new Intl.DateTimeFormat('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date())}
function summaryMarkup(batches){const active=batches.filter(b=>!b.done),total=batches.reduce((n,b)=>n+(Number(b.total)||0),0),finished=batches.reduce((n,b)=>n+(Number(b.success)||0)+(Number(b.failed)||0),0);return `<div class=\"summary-card\"><div class=\"summary-label\">◌ BATCH ĐANG CHẠY</div><div class=\"summary-value\">${active.length}</div><div class=\"summary-sub\">${batches.length} batch được lưu gần đây</div></div><div class=\"summary-card\"><div class=\"summary-label\">▦ ẢNH ĐÃ XỬ LÝ</div><div class=\"summary-value\">${finished}<span class=\"summary-sub\"> / ${total}</span></div><div class=\"summary-sub\">Tổng từ các batch đang hiển thị</div></div><div class=\"summary-card\"><div class=\"summary-label\">✓ CẬP NHẬT THÀNH CÔNG</div><div class=\"summary-value\">${batches.reduce((n,b)=>n+(Number(b.success)||0),0)}</div><div class=\"summary-sub\">Không hiển thị thông tin đăng nhập</div></div>`}
function itemMarkup(x){const kind=status(x.stage),detail=x.detail?`<div class=\"item-detail\" title=\"${esc(x.detail)}\">${esc(x.detail)}</div>`:'';return `<div class=\"item ${kind}\"><div class=\"item-icon\">${icon(kind)}</div><div class=\"item-copy\"><div class=\"item-name\">Ảnh ${String(x.position).padStart(2,'0')}</div><div class=\"item-stage\" title=\"${esc(x.stage)}\">${esc(x.stage)}</div>${detail}</div></div>`}
function batchMarkup(b){const items=Array.isArray(b.items)?b.items:[],complete=(Number(b.success)||0)+(Number(b.failed)||0),total=Math.max(1,Number(b.total)||items.length||1),percent=Math.max(0,Math.min(100,Math.round(complete*100/total))),isDone=Boolean(b.done),isOpen=openBatches.has(b.id),waiting=count(items,'wait'),stateText=isDone?'Hoàn tất':'Đang chạy',buttonText=isOpen?'Thu gọn':'Chi tiết';return `<article class=\"batch ${isDone?'is-done':''} ${isOpen?'expanded':''}\" data-id=\"${esc(b.id)}\"><div class=\"batch-head\"><div class=\"batch-icon\">${isDone?'✓':'↗'}</div><div class=\"batch-copy\"><div class=\"batch-title-row\"><div class=\"batch-title\" title=\"${esc(b.main_acc||'Chưa có tên batch')}\">${esc(b.main_acc||'Chưa có tên batch')}</div><span class=\"state ${isDone?'done':''}\">${stateText}</span></div><div class=\"batch-meta\">Người gửi: ${esc(b.sender_name||'Không rõ')} · ${esc(b.started_at||'')}</div></div><button class=\"toggle\" type=\"button\" data-toggle=\"${esc(b.id)}\" aria-expanded=\"${isOpen}\">${buttonText}</button></div><div class=\"batch-body\"><div class=\"progress-top\"><span class=\"progress-label\">${esc(b.stage||'Đang chuẩn bị')}</span><span class=\"progress-numbers\"><strong>${complete}/${total}</strong> ảnh</span></div><div class=\"bar\" role=\"progressbar\" aria-valuemin=\"0\" aria-valuemax=\"${total}\" aria-valuenow=\"${complete}\"><div class=\"fill\" style=\"width:${percent}%\"></div></div><div class=\"result-row\"><span class=\"metric ok\">✓ ${Number(b.success)||0} thành công</span>${Number(b.failed)?`<span class=\"metric fail\">! ${Number(b.failed)} lỗi</span>`:''}${waiting?`<span class=\"metric\">· ${waiting} đang chờ</span>`:''}</div><div class=\"items-panel\"><div class=\"items\">${items.map(itemMarkup).join('')}</div></div></div></article>`}
function emptyMarkup(kind){const text=kind==='done'?['Chưa có batch hoàn tất','Các batch xong sẽ được lưu tại đây trong phiên hiện tại.']:kind==='active'?['Không có batch nào đang chạy','Bot đang chờ album ảnh và danh sách giá mới.']:['Chưa có batch nào','Tiến độ xử lý sẽ xuất hiện ở đây khi bot bắt đầu làm việc.'];return `<div class=\"empty\"><div class=\"empty-icon\">◌</div><strong>${text[0]}</strong><p>${text[1]}</p></div>`}
function render(data){const all=Array.isArray(data.batches)?data.batches:[];summary.innerHTML=summaryMarkup(all);const batches=filter==='active'?all.filter(b=>!b.done):filter==='done'?all.filter(b=>b.done):all;app.innerHTML=batches.length?batches.map(batchMarkup).join(''):emptyMarkup(filter);sync.textContent=`Đồng bộ lúc ${formatTime()}`;app.querySelectorAll('[data-toggle]').forEach(button=>button.addEventListener('click',()=>{const id=button.dataset.toggle;openBatches.has(id)?openBatches.delete(id):openBatches.add(id);const card=button.closest('.batch');card.classList.toggle('expanded',openBatches.has(id));button.setAttribute('aria-expanded',String(openBatches.has(id)));button.textContent=openBatches.has(id)?'Thu gọn':'Chi tiết'}))}
document.querySelectorAll('.filter').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('selected',x===button));load()}));
async function load(){try{const response=await fetch('/api/progress',{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);render(await response.json())}catch(_){summary.innerHTML='';app.innerHTML='<div class=\"empty\"><div class=\"empty-icon\">!</div><strong>Không đọc được tiến độ</strong><p>Bot có thể vừa khởi động lại. Trang sẽ tự thử lại sau.</p></div>';sync.textContent='Đang thử kết nối lại…'}}load();setInterval(load,800);
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
