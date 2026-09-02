"""Small, dependency-free operational dashboard for the Zalo importer."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ProgressTracker:
    """Bounded in-memory status; it never stores credentials or image URLs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._batches: list[dict[str, Any]] = []
        self._issues: list[dict[str, Any]] = []

    def start(self, total: int, sender_name: str, main_acc: str) -> str:
        batch_id = f"{int(time.time() * 1000)}-{len(self._batches)}"
        batch = {
            "id": batch_id, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sender_name": sender_name, "main_acc": main_acc, "total": total,
            "success": 0, "failed": 0, "stage": "Đang chuẩn bị", "done": False,
            "issues": [],
            "items": [{"position": i, "stage": "Chờ xử lý", "detail": ""} for i in range(1, total + 1)],
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
            if not batch or not 1 <= position <= len(batch["items"]):
                return
            batch["items"][position - 1].update(stage=stage, detail=detail[:180])

    def record_issue(self, title: str, detail: str, *, batch_id: str | None = None, severity: str = "error") -> None:
        issue = {
            "id": f"{int(time.time() * 1000)}-{len(self._issues)}", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "title": title[:160], "detail": detail[:500], "severity": severity, "batch_id": batch_id,
        }
        with self._lock:
            self._issues.insert(0, issue)
            del self._issues[80:]
            batch = self._find(batch_id)
            if batch:
                batch["issues"].insert(0, issue)
                del batch["issues"][8:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps({"batches": self._batches, "issues": self._issues}, ensure_ascii=False))

    def _find(self, batch_id: str | None) -> dict[str, Any] | None:
        return next((batch for batch in self._batches if batch["id"] == batch_id), None)


PAGE = r"""<!doctype html>
<html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Zalo Bot</title>
<style>
:root{color-scheme:dark;--bg:#080808;--card:#121212;--line:#303030;--muted:#999;--text:#f5f5f5;--r:15px}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}.shell{width:min(1120px,calc(100% - 32px));margin:auto;padding:34px 0 60px}.top{display:flex;justify-content:space-between;gap:18px;align-items:center;padding-bottom:26px;border-bottom:1px solid var(--line)}.brand{display:flex;align-items:center;gap:12px}.logo{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:#fff;color:#000;font-weight:900;font-size:17px}.eyebrow{margin:0;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.15em}.title{margin:2px 0 0;font-size:22px;letter-spacing:-.04em}.live{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:99px;padding:7px 10px;color:#ddd;font-size:10px;font-weight:800;letter-spacing:.08em}.dot{width:7px;height:7px;border-radius:99px;background:#fff;animation:pulse 1.8s infinite}.nav{display:flex;gap:5px;padding:18px 0}.nav a{border:1px solid transparent;border-radius:9px;padding:8px 11px;color:var(--muted);font-size:12px;font-weight:800;text-decoration:none}.nav a.active,.nav a:hover{border-color:var(--line);background:#1c1c1c;color:#fff}.badge{display:inline-grid;place-items:center;min-width:17px;height:17px;margin-left:4px;border-radius:99px;background:#fff;color:#111;font-size:10px}.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:6px 0 22px}.stat{border:1px solid var(--line);border-radius:var(--r);background:var(--card);padding:15px 16px}.stat label{display:block;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.08em}.stat strong{display:block;margin-top:3px;font-size:26px;letter-spacing:-.05em}.stat small{color:#777;font-size:11px}.head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:20px 0 10px}.head h2{margin:0;font-size:14px}.filters{display:flex;gap:5px}.btn{border:1px solid var(--line);border-radius:8px;padding:6px 9px;background:transparent;color:var(--muted);font:inherit;font-size:11px;font-weight:800;cursor:pointer}.btn.active,.btn:hover{background:#eee;border-color:#eee;color:#111}.list{display:grid;gap:10px}.batch,.issue,.log{border:1px solid var(--line);border-radius:var(--r);background:var(--card);overflow:hidden}.batch.has-issue{border-color:#777}.batch-top{display:flex;align-items:center;gap:11px;padding:15px 16px}.mark{display:grid;place-items:center;width:32px;height:32px;border:1px solid var(--line);border-radius:9px;background:#0c0c0c;font-size:12px;font-weight:900}.copy{min-width:0;flex:1}.name,.log-name{overflow:hidden;font-size:15px;font-weight:850;white-space:nowrap;text-overflow:ellipsis}.meta{overflow:hidden;margin-top:2px;color:var(--muted);font-size:11px;white-space:nowrap;text-overflow:ellipsis}.state{border:1px solid var(--line);border-radius:99px;padding:4px 7px;color:#ddd;font-size:10px;font-weight:850}.batch.is-done .state{background:#fff;color:#000;border-color:#fff}.batch-body{padding:0 16px 15px}.progress{display:flex;justify-content:space-between;gap:12px;font-size:12px;font-weight:750}.bar{height:7px;margin:8px 0 10px;border-radius:99px;overflow:hidden;background:#2a2a2a}.fill{height:100%;background:#fff;transition:width .3s}.facts{display:flex;flex-wrap:wrap;gap:6px}.fact{border-radius:6px;background:#222;color:#d4d4d4;padding:4px 7px;font-size:10px;font-weight:750}.fact.bad{background:#eee;color:#111}.items,.batch-issues{display:none;margin-top:13px;padding-top:13px;border-top:1px solid var(--line)}.batch.open .items,.batch.open .batch-issues{display:block}.items{grid-template-columns:repeat(auto-fill,minmax(135px,1fr));gap:7px}.item{border:1px solid #292929;border-radius:9px;background:#0d0d0d;padding:9px}.item b{font-size:11px}.item span{display:block;overflow:hidden;margin-top:2px;color:var(--muted);font-size:10px;white-space:nowrap;text-overflow:ellipsis}.item.ok span{color:#fff}.item.fail{border-color:#aaa}.issue{padding:13px 14px}.issue-title{font-weight:850}.issue-detail{margin-top:3px;color:var(--muted);font-size:12px;white-space:pre-wrap}.issue-meta{margin-top:7px;color:#777;font-size:10px}.log summary{display:flex;align-items:center;gap:10px;padding:12px 14px;cursor:pointer;list-style:none}.log summary::-webkit-details-marker{display:none}.time{color:#888;font:11px ui-monospace,SFMono-Regular,Consolas,monospace}.log-name{flex:1;font-size:12px}.log pre{max-height:330px;overflow:auto;margin:0;padding:13px 14px;border-top:1px solid var(--line);background:#0b0b0b;color:#ddd;font:11px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap}.empty{border:1px dashed #444;border-radius:var(--r);padding:52px 18px;color:var(--muted);text-align:center}.empty strong{display:block;color:#eee;font-size:14px}.empty p{margin:4px 0 0;font-size:12px}@keyframes pulse{50%{opacity:.35}}@media(max-width:650px){.shell{width:min(100% - 20px,1120px);padding-top:20px}.title{font-size:19px}.live{font-size:0;padding:10px}.summary{gap:6px}.stat{padding:12px 10px}.stat label{font-size:9px}.stat strong{font-size:21px}.stat small,.state{display:none}.batch-top{padding:13px;gap:8px}.batch-body{padding:0 13px 13px}.items{grid-template-columns:repeat(2,1fr)}}
</style><body><main class="shell"><header class="top"><div class="brand"><div class="logo">Z</div><div><p class="eyebrow">ZALO BOT / OPERATIONS</p><h1 class="title" id="title">Bảng điều khiển</h1></div></div><div class="live"><i class="dot"></i>TRỰC TUYẾN</div></header><nav class="nav" id="nav"></nav><section class="summary" id="summary"></section><section id="content"></section></main>
<script>
const route=location.pathname,content=document.querySelector('#content'),nav=document.querySelector('#nav'),summary=document.querySelector('#summary');let filter='active',open=new Set(),last={};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const issue=b=>Number(b.failed)>0||(b.issues||[]).length>0;const itemType=s=>/hoàn tất|đã cập nhật/i.test(s||'')?'ok':/lỗi|thất bại|bỏ/i.test(s||'')?'fail':'wait';
function setNav(d){let count=(d.issues||[]).length;nav.innerHTML=`<a class="${route==='/'?'active':''}" href="/">Tiến độ</a><a class="${route==='/issues'?'active':''}" href="/issues">Đã lỗi${count?`<span class="badge">${count}</span>`:''}</a>`}
function setSummary(d){let b=d.batches||[],ok=b.reduce((n,x)=>n+(+x.success||0),0);summary.innerHTML=`<div class="stat"><label>BATCH ĐANG CHẠY</label><strong>${b.filter(x=>!x.done).length}</strong><small>${b.filter(x=>x.done).length} batch đã hoàn thành</small></div><div class="stat"><label>ẢNH CẬP NHẬT</label><strong>${ok}</strong><small>Tổng ảnh thành công trong phiên</small></div><div class="stat"><label>CẦN KIỂM TRA</label><strong>${(d.issues||[]).length}</strong><small>Lỗi update hoặc list bất thường</small></div>`}
function issueCard(x){return `<article class="issue"><div class="issue-title">${esc(x.title)}</div><div class="issue-detail">${esc(x.detail)}</div><div class="issue-meta">${esc(x.at)} · ${x.severity==='warning'?'Cảnh báo':'Lỗi'}</div></article>`}
function batchCard(b){let total=Math.max(1,+b.total||1),done=(+b.success||0)+(+b.failed||0),percent=Math.min(100,Math.round(done*100/total)),opened=open.has(b.id),issues=b.issues||[];return `<article class="batch ${b.done?'is-done':''} ${issue(b)?'has-issue':''} ${opened?'open':''}"><div class="batch-top"><div class="mark">${b.done?'✓':'↗'}</div><div class="copy"><div class="name">${esc(b.main_acc)}</div><div class="meta">Người gửi: ${esc(b.sender_name)} · ${esc(b.started_at)}</div></div><span class="state">${b.done?'Hoàn tất':'Đang chạy'}</span><button class="btn" data-open="${esc(b.id)}">${opened?'Thu gọn':'Chi tiết'}</button></div><div class="batch-body"><div class="progress"><span>${esc(b.stage)}</span><span>${done}/${total} ảnh</span></div><div class="bar"><div class="fill" style="width:${percent}%"></div></div><div class="facts"><span class="fact">${b.success||0} thành công</span>${b.failed?`<span class="fact bad">${b.failed} lỗi ảnh</span>`:''}${issues.length?`<span class="fact bad">${issues.length} cảnh báo</span>`:''}</div><div class="batch-issues">${issues.map(issueCard).join('')}</div><div class="items">${(b.items||[]).map(x=>`<div class="item ${itemType(x.stage)}"><b>Ảnh ${String(x.position).padStart(2,'0')}</b><span title="${esc(x.detail||x.stage)}">${esc(x.stage)}</span></div>`).join('')}</div></div></article>`}
function dashboard(d){let all=d.batches||[],b=filter==='active'?all.filter(x=>!x.done):filter==='done'?all.filter(x=>x.done):all;content.innerHTML=`<div class="head"><h2>Tiến độ batch</h2><div class="filters"><button class="btn ${filter==='active'?'active':''}" data-filter="active">Đang chạy</button><button class="btn ${filter==='all'?'active':''}" data-filter="all">Tất cả</button><button class="btn ${filter==='done'?'active':''}" data-filter="done">Đã xong</button></div></div><div class="list">${b.length?b.map(batchCard).join(''):'<div class="empty"><strong>Không có batch phù hợp</strong><p>Bot đang chờ ảnh và bảng giá mới.</p></div>'}</div>`;content.querySelectorAll('[data-open]').forEach(x=>x.onclick=()=>{open.has(x.dataset.open)?open.delete(x.dataset.open):open.add(x.dataset.open);dashboard(last)});content.querySelectorAll('[data-filter]').forEach(x=>x.onclick=()=>{filter=x.dataset.filter;dashboard(last)})}
function issues(d){let rows=d.issues||[];content.innerHTML=`<div class="head"><h2>Đã lỗi / cần kiểm tra</h2></div><div class="list">${rows.length?rows.map(issueCard).join(''):'<div class="empty"><strong>Chưa có lỗi nào</strong><p>Lỗi update hoặc list có ảnh/giá bất thường sẽ hiện tại đây.</p></div>'}</div>`}
function render(d){last=d;setNav(d);setSummary(d);document.querySelector('#title').textContent=route==='/issues'?'Đã lỗi / cần kiểm tra':'Bảng điều khiển';route==='/issues'?issues(d):dashboard(d)}async function load(){try{let r=await fetch('/api/progress',{cache:'no-store'});if(!r.ok)throw Error();render(await r.json())}catch{content.innerHTML='<div class="empty"><strong>Không đọc được tiến độ</strong><p>Bot có thể vừa khởi động lại. Trang sẽ tự thử lại.</p></div>'}}load();setInterval(load,800);
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
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/api/progress":
                content = json.dumps(tracker.snapshot(), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            elif path in ("/", "/index.html", "/issues"):
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
    return f"http://<IP-VPS-CUA-BAN>:{port}" if host in {"0.0.0.0", "::"} else f"http://{host}:{port}"
