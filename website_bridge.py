"""Import Zalo image batches directly into Cloudinary and Supabase."""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from zlapi import ImageGroup
from progress_dashboard import ProgressTracker

DESCRIPTION = "Hỗ trợ ae góp chỉ từ 30% giá acc ( Ấn chức năng 'Tính góp' để tính góp )."
OWNER_LINE = re.compile(r"^\s*(?:chủ|chu|owner|main|tk|tên|ten)(?:\s*(?:tk|acc|nick|sở\s*hữu|so\s*huu))?\s*[:=-]\s*(.+?)\s*$", re.I)


def price_text_without_owner(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not OWNER_LINE.match(line))


PRICE_TOKEN = re.compile(r"^(?:\d+(?:[.,]\d+)?(?:m\d*|k|tr\d*|triệu\d*|trieu\d*)?|\d{1,3}(?:[.,]\d{3})+)$", re.I)
MISSING_ACCOUNT_TOKENS = {"bay"}
MISSING_ACCOUNT_PRICE = 999_000_000


def parse_price_token(token: str) -> int | None:
    """Parse one accepted price token into VND without guessing prose."""
    value = token.lower().strip(".,;:()[]{}")
    value = re.sub(r"triệu|trieu|tr", "m", value)
    # `2.800.000` and `2,800,000` are full VND values, not decimals.
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", value):
        value = re.sub(r"[.,]", "", value)
    else:
        value = value.replace(",", ".")
    try:
        if value.endswith("k"):
            return round(float(value[:-1]) * 1_000)
        if "m" in value:
            whole, fraction = value.split("m", 1)
            return round((float(whole or 0) + (float("0." + fraction) if fraction else 0)) * 1_000_000)
        number = float(value)
        return round(number * (1_000_000 if number < 1_000 else 1))
    except ValueError:
        return None


def parse_prices(text: str) -> list[int]:
    # A group chat often has a free-form description followed by price lines such
    # as `162 (Qt)`.  Ignore descriptions and annotations; accept only lines made
    # entirely of price tokens, so ordinary sentences containing a number stay out.
    raw_tokens: list[str] = []
    has_real_price = False
    for line in price_text_without_owner(text).splitlines():
        # Some sellers omit the final `)` in notes like `27 (VNG`; annotations
        # must not make that otherwise valid price disappear.
        line = re.sub(r"\([^)]*(?:\)|$)|\[[^]]*(?:\]|$)", " ", line).strip()
        # Optional `giá:` prefix and common list markers (`1. 2m8`, `• 2m8`).
        line = re.sub(r"^(?:giá|gia|price)\s*[:=-]?\s*", "", line, flags=re.I)
        line = re.sub(r"^(?:[#•*]\s*|\d+[.)]\s+)", "", line)
        line = re.sub(r"(\d+(?:[.,]\d+)?)\s+(m|k)\b", r"\1\2", line, flags=re.I)
        line = re.sub(r"(\d+(?:[.,]\d+)?)\s*(?:triệu|trieu|tr)\b", r"\1tr", line, flags=re.I)
        tokens = re.sub(r"\s*[-|;/]\s*", " ", line).split()
        normalized = [token.strip(".,;:()[]{}").lower() for token in tokens]
        if tokens and all(PRICE_TOKEN.fullmatch(token) or token in MISSING_ACCOUNT_TOKENS for token in normalized):
            raw_tokens.extend(normalized)
            has_real_price = has_real_price or any(PRICE_TOKEN.fullmatch(token) for token in normalized)
    if not has_real_price:
        return []
    prices = []
    for token in raw_tokens:
        if token in MISSING_ACCOUNT_TOKENS:
            prices.append(MISSING_ACCOUNT_PRICE)
            continue
        price = parse_price_token(token)
        if price and price > 0:
            prices.append(price)
    return prices


def raised_price(price: int) -> int:
    if price == MISSING_ACCOUNT_PRICE:
        return price
    millions = price / 1_000_000
    for limit, increase in ((3, 300_000), (7, 500_000), (10, 600_000), (15, 800_000), (20, 900_000), (50, 2_000_000), (70, 3_000_000), (100, 4_000_000)):
        if millions < limit:
            return price + increase
    return price + 5_000_000


def format_vnd(price: int) -> str:
    return f"{price:,}".replace(",", ".") + " VND"


def format_price_badge(price: int) -> str:
    """Format VND in the compact sale style used on the returned image."""
    millions, remainder = divmod(price, 1_000_000)
    if remainder == 0:
        return f"{millions}m"
    fraction = f"{remainder:06d}".rstrip("0")
    return f"{millions}m{fraction}"


def image_urls(message: Any) -> list[str]:
    if isinstance(message, ImageGroup):
        return [str(image.href) for image in message.images if getattr(image, "href", None)]
    url = message.get("href") if isinstance(message, dict) else getattr(message, "href", None)
    return [str(url)] if url else []


def main_acc_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = OWNER_LINE.match(line)
        if match and match.group(1).strip():
            return match.group(1).strip()[:200]
    # Descriptions commonly precede the prices in group chats.  They are not an
    # owner declaration; without an explicit `chủ:`/`tên:` line, the sender is
    # the only reliable account owner.
    return fallback[:200]


def should_retry(error: BaseException) -> bool:
    """Retry temporary transport/server failures, not permanent bad requests."""
    if isinstance(error, requests.HTTPError) and error.response is not None:
        status = error.response.status_code
        return status in {408, 425, 429} or status >= 500
    return isinstance(error, (OSError, ValueError, requests.RequestException))


def retry(action: Callable[[], Any], label: str) -> Any:
    """Retry only transient image failures, leaving five seconds between tries."""
    error = None
    for attempt in range(1, 4):
        try:
            return action()
        except (OSError, ValueError, requests.RequestException) as caught:
            error = caught
            if attempt < 3 and should_retry(caught):
                print(f"[BATCH] {label} lỗi, thử lại lần {attempt + 1}/3 sau 5 giây.", flush=True)
                time.sleep(5)
                continue
            break
    suffix = " sau 3 lần" if attempt == 3 else ""
    raise RuntimeError(f"{label} thất bại{suffix}: {error}")


IMAGE_HASH_BITS = 16
IMAGE_HASH_DISTANCE_LIMIT = 30


def _difference_hash(image: Image.Image) -> str:
    """Return a 256-bit dHash; resilient to JPEG recompression and resizing."""
    reduced = ImageOps.grayscale(image).resize((IMAGE_HASH_BITS + 1, IMAGE_HASH_BITS), Image.Resampling.LANCZOS)
    pixels = list(reduced.get_flattened_data())
    value = 0
    for row in range(IMAGE_HASH_BITS):
        offset = row * (IMAGE_HASH_BITS + 1)
        for column in range(IMAGE_HASH_BITS):
            value = (value << 1) | (pixels[offset + column] > pixels[offset + column + 1])
    return f"{value:0{IMAGE_HASH_BITS * IMAGE_HASH_BITS // 4}x}"


def image_signature(content: bytes) -> str:
    """Hash four border regions, intentionally excluding the centred price badge."""
    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    # The bot places the price in the centre.  Top, bottom, and side strips stay
    # stable even when the same account image arrives again with a price pasted on.
    crops = (
        (0, 0, width, max(1, int(height * 0.28))),
        (0, max(0, int(height * 0.72)), width, height),
        (0, int(height * 0.20), max(1, int(width * 0.28)), max(1, int(height * 0.80))),
        (max(0, int(width * 0.72)), int(height * 0.20), width, max(1, int(height * 0.80))),
    )
    return json.dumps({"v": 1, "h": [_difference_hash(image.crop(box)) for box in crops]}, separators=(",", ":"))


def signature_similarity(candidate: str, stored: str) -> int | None:
    """Return confidence 0-100 for compatible image signatures, else None."""
    try:
        candidate_hashes = json.loads(candidate).get("h", [])
        stored_hashes = json.loads(stored).get("h", [])
        if len(candidate_hashes) != 4 or len(stored_hashes) != 4:
            return None
        distances = [(int(left, 16) ^ int(right, 16)).bit_count() for left, right in zip(candidate_hashes, stored_hashes)]
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    # One edge may be cropped by a sender.  Compare the three most stable sides
    # instead of allowing a centre badge or minor crop to reject a true match.
    average_distance = sum(sorted(distances)[:3]) / 3
    if average_distance > IMAGE_HASH_DISTANCE_LIMIT:
        return None
    return round(100 * (1 - average_distance / (IMAGE_HASH_BITS * IMAGE_HASH_BITS)))


def priced_image(content: bytes, price: int, max_dimension: int = 1920) -> bytes:
    """Create the red, centred sale-price badge used only for Zalo returns."""
    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    # This copy is sent only through Zalo.  Keeping its longest edge bounded
    # avoids spending most of the batch time encoding a 4K/8K duplicate, while
    # the untouched full-resolution original is still what Cloudinary receives.
    if max_dimension > 0 and max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    width, height = image.size
    label = format_price_badge(price)
    # Keep the price readable without obscuring the account details.  The badge
    # occupies roughly the compact proportion shown in the reference image.
    size = max(28, min(width, height) // 16)
    font = None
    for font_path in (
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        try:
            font = ImageFont.truetype(font_path, size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(image, "RGBA")
    box = draw.textbbox((0, 0), label, font=font)
    padding_x, padding_y = max(12, size // 5), max(8, size // 7)
    badge_width = box[2] - box[0] + padding_x * 2
    badge_height = box[3] - box[1] + padding_y * 2
    left = (width - badge_width) // 2
    top = (height - badge_height) // 2
    # Straight saturated-red panel and large white type mirror the supplied sample.
    draw.rectangle((left, top, left + badge_width, top + badge_height), fill=(230, 0, 0, 255))
    text_x = left + padding_x - box[0]
    text_y = top + padding_y - box[1]
    draw.text((text_x, text_y), label, font=font, fill=(255, 255, 255, 255))
    output = io.BytesIO()
    # This is only the temporary Zalo return image.  Faster JPEG encoding keeps
    # the label clear while the original full-quality bytes remain on the web.
    image.save(output, format="JPEG", quality=82, optimize=False)
    return output.getvalue()


@dataclass
class PendingBatch:
    sender_id: str
    sender_name: str
    images: list[str] = field(default_factory=list)
    price_text: str | None = None
    return_callback: Callable[[list[tuple[bytes, str]], dict[str, Any]], None] | None = None
    reported_mismatch: str | None = None


class WebsiteBridge:
    def __init__(self, settings: dict[str, Any] | None, progress: ProgressTracker | None = None):
        self.settings = settings or {}
        self.batches: dict[tuple[str, str], PendingBatch] = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        # Rendering is CPU-heavy, whereas downloads/uploads are I/O-heavy.  A
        # small separate pool lets Cloudinary work while a labelled return copy
        # is encoded, without oversubscribing a small VPS.
        self.render_executor = ThreadPoolExecutor(max_workers=2)
        self.progress = progress or ProgressTracker()
        self._http_local = threading.local()

    def _http(self) -> requests.Session:
        """One persistent connection pool per worker thread."""
        session = getattr(self._http_local, "session", None)
        if session is None:
            session = requests.Session()
            self._http_local.session = session
        return session

    def receive(self, mid, author_id, message, details, thread_id, thread_type, return_callback=None) -> None:
        urls = image_urls(message)
        is_prices = isinstance(message, str) and bool(parse_prices(message))
        if not urls and not is_prices:
            return
        key = (str(author_id), str(thread_id))
        with self.lock:
            batch = self.batches.setdefault(key, PendingBatch(key[0], details.get("dName") or "(không rõ)"))
            batch.images.extend(url for url in urls if url not in batch.images)
            if is_prices:
                batch.price_text = message
                batch.sender_name = details.get("dName") or batch.sender_name
            if return_callback:
                batch.return_callback = return_callback
            prices = parse_prices(batch.price_text or "")
            print(f"[BATCH] {key[0]}: {len(batch.images)} ảnh / {len(prices)} giá", flush=True)
            if len(batch.images) >= 2 and len(prices) >= 2 and len(batch.images) != len(prices):
                signature = f"{len(batch.images)}:{len(prices)}"
                if batch.reported_mismatch != signature:
                    self.progress.record_issue(
                        "Số lượng ảnh và giá không khớp",
                        f"Người gửi {batch.sender_name}: {len(batch.images)} ảnh nhưng có {len(prices)} giá. Batch được giữ lại để chờ dữ liệu khớp.",
                        severity="warning",
                    )
                    batch.reported_mismatch = signature
            if len(batch.images) < 2 or len(prices) < 2 or len(batch.images) != len(prices):
                return
            batch = self.batches.pop(key)
        self.executor.submit(self._import, batch)

    def _headers(self) -> dict[str, str]:
        key = self.settings.get("supabase_service_key", "")
        return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}

    def resolve_account_owner(self, base: str) -> str | None:
        """Use configured web-user ID, or the newest existing account's owner."""
        configured = str(self.settings.get("account_owner") or "").strip()
        if configured:
            return configured
        response = self._http().get(
            f"{base}/accounts",
            params={"select": "owner", "owner": "not.is.null", "order": "id.desc", "limit": "1"},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        rows = response.json()
        if rows and rows[0].get("owner"):
            return str(rows[0]["owner"])
        return None

    def find_existing_accounts(self, base: str, titles: list[str]) -> dict[str, str]:
        """Look up all stable image titles in one Supabase request per batch."""
        if not titles:
            return {}
        response = self._http().get(
            f"{base}/accounts",
            params={"select": "id,title", "title": f"in.({','.join(titles)})"},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return {
            str(row["title"]): str(row["id"])
            for row in response.json()
            if row.get("title") and row.get("id")
        }

    def find_owner_by_image(self, source_url: str) -> dict[str, Any] | None:
        """Match a priced/unpriced image against stored perceptual hashes."""
        required = ("supabase_url", "supabase_service_key")
        if any(not self.settings.get(key) for key in required):
            raise RuntimeError("Thiếu cấu hình Supabase để tìm chủ acc.")
        session = self._http()
        response = session.get(source_url, timeout=60)
        response.raise_for_status()
        candidate = image_signature(response.content)
        base = str(self.settings["supabase_url"]).rstrip("/") + "/rest/v1"
        response = session.get(
            f"{base}/accounts",
            params={
                "select": "id,title,main_acc,image_hash",
                "image_hash": "not.is.null",
                "limit": "1000",
                "order": "id.desc",
            },
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        best: dict[str, Any] | None = None
        for row in response.json():
            confidence = signature_similarity(candidate, str(row.get("image_hash") or ""))
            if confidence is None:
                continue
            if best is None or confidence > best["confidence"]:
                best = {
                    "account_id": row.get("id"),
                    "title": row.get("title"),
                    "main_acc": row.get("main_acc") or "(chưa có tên chủ)",
                    "confidence": confidence,
                }
        return best

    def _upload_and_store(self, base: str, source_url: str, price: int, title: str, main_acc: str, owner: str | None, batch_id: str, position: int, existing_id: str | None, lookup_per_item: bool) -> bytes:
        session = self._http()

        def download_original():
            self.progress.update_item(batch_id, position, "Đang tải và dán giá")
            response = session.get(source_url, timeout=60)
            response.raise_for_status()
            return response.content

        original = retry(download_original, f"Ảnh {title}")
        # Save a watermark-tolerant fingerprint of the original alongside the
        # account.  `/timchu` later compares its untouched border regions.
        image_hash = image_signature(original)
        try:
            return_size = int(self.settings.get("return_image_max_dimension", 1920))
        except (TypeError, ValueError):
            return_size = 1920
        # Start producing the Zalo-only image now, while this worker uploads the
        # untouched original to Cloudinary and writes the account row.
        rendered_future = self.render_executor.submit(
            retry,
            lambda: priced_image(original, price, max(0, return_size)),
            f"Dán giá ảnh {title}",
        )

        def upload_image():
            self.progress.update_item(batch_id, position, "Đang upload ảnh gốc")
            response = session.post(
                f"https://api.cloudinary.com/v1_1/{self.settings['cloudinary_cloud_name']}/image/upload",
                # Match the previously working unsigned-upload request shape.
                files={"file": (f"{title}.jpg", original)},
                # Unsigned presets often reject `overwrite`; Cloudinary already
                # handles a duplicate public ID according to the preset policy.
                data={"upload_preset": self.settings["cloudinary_upload_preset"], "public_id": f"zalo_bot/{title}"},
                timeout=90,
            )
            if not response.ok:
                try:
                    detail = response.json().get("error", {}).get("message")
                except ValueError:
                    detail = response.text[:300]
                raise requests.HTTPError(
                    f"Cloudinary HTTP {response.status_code}: {detail or 'không rõ lỗi'}",
                    response=response,
                )
            return response

        upload = retry(upload_image, f"Upload ảnh {title}")
        image_url = upload.json()["secure_url"]
        self.progress.update_item(batch_id, position, "Đang lưu dữ liệu web")
        row = {"price": str(price), "description": DESCRIPTION, "image_url": image_url, "main_acc": main_acc, "image_hash": image_hash}
        if owner:
            row["owner"] = owner
        if lookup_per_item:
            # A one-shot batch lookup failed; fall back to the exact former
            # per-item behavior rather than risking an accidental duplicate.
            existing = session.get(f"{base}/accounts", params={"select": "id", "title": f"eq.{title}"}, headers=self._headers(), timeout=30)
            existing.raise_for_status()
            rows = existing.json()
            existing_id = str(rows[0]["id"]) if rows else None
        if existing_id:
            account_id = existing_id
            updated = session.patch(f"{base}/accounts", params={"id": f"eq.{account_id}"}, json=row, headers=self._headers(), timeout=30)
            updated.raise_for_status()
        else:
            created = session.post(f"{base}/accounts", json={"title": title, **row}, headers=self._headers(), timeout=30)
            created.raise_for_status()
            account_id = created.json()[0]["id"]
            linked = session.post(f"{base}/account_images", json={"acc_id": account_id, "image_url": image_url}, headers=self._headers(), timeout=30)
            linked.raise_for_status()
        rendered = rendered_future.result()
        self.progress.update_item(batch_id, position, "Hoàn tất")
        return rendered

    def _import(self, batch: PendingBatch) -> None:
        required = ("supabase_url", "supabase_service_key", "cloudinary_cloud_name", "cloudinary_upload_preset")
        if any(not self.settings.get(key) for key in required):
            print("[BATCH] Thiếu cấu hình Supabase/Cloudinary trong website.js.", flush=True)
            self.progress.record_issue("Thiếu cấu hình website", "Không thể cập nhật list vì website.js thiếu Supabase hoặc Cloudinary.")
            return
        prices = [raised_price(value) for value in parse_prices(batch.price_text or "")]
        base = str(self.settings["supabase_url"]).rstrip("/") + "/rest/v1"
        main_acc = main_acc_from_text(batch.price_text or "", batch.sender_name)
        batch_id = self.progress.start(len(prices), batch.sender_name, main_acc)
        self.progress.update_batch(batch_id, "Đang tìm owner web")
        try:
            owner = self.resolve_account_owner(base)
        except (ValueError, requests.RequestException) as error:
            print(f"[BATCH] Không đọc được owner nội bộ của web: {error}", flush=True)
            self.progress.record_issue("Không đọc được owner web", str(error), batch_id=batch_id, severity="warning")
            owner = None
        tasks = []
        for position, (source_url, price) in enumerate(zip(batch.images, prices), start=1):
            digest = hashlib.sha256(f"{batch.sender_id}:{source_url}".encode()).hexdigest()[:12]
            title = f"fat_{digest}"
            tasks.append((position, source_url, price, title))

        self.progress.update_batch(batch_id, "Đang kiểm tra account đã có")
        try:
            existing_ids: dict[str, str] | None = self.find_existing_accounts(base, [title for _, _, _, title in tasks])
        except (ValueError, requests.RequestException) as error:
            # Retain the old per-item query path if the accelerated lookup is
            # temporarily unavailable, so no storage logic is lost.
            print(f"[BATCH] Không kiểm tra nhanh được account cũ, dùng chế độ dự phòng: {error}", flush=True)
            self.progress.record_issue("Kiểm tra account cũ chậm", str(error), batch_id=batch_id, severity="warning")
            existing_ids = None

        # Downloads, Cloudinary uploads, and independent account rows can run in
        # parallel.  Results are put back by position before the Zalo album is
        # returned, so concurrency never changes image-to-price pairing.
        completed: dict[int, tuple[bytes, str]] = {}
        requested_workers = self.settings.get("batch_workers", 6)
        try:
            worker_count = max(1, min(6, int(requested_workers), len(tasks)))
        except (TypeError, ValueError):
            worker_count = min(6, len(tasks))
        self.progress.update_batch(batch_id, f"Đang xử lý song song {worker_count} ảnh")
        with ThreadPoolExecutor(max_workers=worker_count) as workers:
            futures = {
                workers.submit(
                    self._upload_and_store,
                    base,
                    source_url,
                    price,
                    title,
                    main_acc,
                    owner,
                    batch_id,
                    position,
                    existing_ids.get(title) if existing_ids is not None else None,
                    existing_ids is None,
                ): (position, price)
                for position, source_url, price, title in tasks
            }
            for finished_count, future in enumerate(as_completed(futures), start=1):
                position, price = futures[future]
                try:
                    completed[position] = (future.result(), f"{position:03d}.jpg")
                except (KeyError, OSError, ValueError, requests.RequestException, RuntimeError) as error:
                    print(f"[BATCH] Bỏ ảnh vị trí {position} (giá {format_vnd(price)}): {error}", flush=True)
                    self.progress.update_item(batch_id, position, "Lỗi", str(error))
                    self.progress.record_issue(
                        f"Không cập nhật được ảnh {position}",
                        f"Giá {format_vnd(price)}: {error}",
                        batch_id=batch_id,
                    )
                self.progress.update_batch(
                    batch_id,
                    "Đang xử lý ảnh",
                    success=len(completed),
                    failed=finished_count - len(completed),
                )
        returned = [completed[position] for position, _, _, _ in tasks if position in completed]
        if not returned:
            self.progress.update_batch(batch_id, "Thất bại: không có ảnh cập nhật", success=0, failed=len(tasks), done=True)
            self.progress.record_issue("Batch không cập nhật được ảnh nào", "Toàn bộ ảnh trong list thất bại. Xem lỗi từng ảnh để xử lý.", batch_id=batch_id)
            print("[BATCH] Không ảnh nào được cập nhật, không trả album.", flush=True)
            return
        owner_status = "đã gán" if owner else "chưa tìm thấy"
        print(f"[BATCH] Đã cập nhật {len(returned)}/{len(prices)} account | Chủ acc: {main_acc} | Owner web: {owner_status}", flush=True)
        self.progress.update_batch(batch_id, "Đã cập nhật web, đang trả album Zalo", success=len(returned), failed=len(tasks) - len(returned))
        if batch.return_callback:
            batch.return_callback(returned, {
                "count": len(returned),
                "main_acc": main_acc,
                "sender_id": batch.sender_id,
                "sender_name": batch.sender_name,
            })
        self.progress.update_batch(batch_id, "Hoàn tất", success=len(returned), failed=len(tasks) - len(returned), done=True)
