"""Import Zalo image batches directly into Cloudinary and Supabase."""
from __future__ import annotations

import hashlib
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from zlapi import ImageGroup

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


def retry(action: Callable[[], Any], label: str) -> Any:
    """Try an image action three times, leaving five seconds between retries."""
    error = None
    for attempt in range(1, 4):
        try:
            return action()
        except (OSError, ValueError, requests.RequestException) as caught:
            error = caught
            if attempt < 3:
                print(f"[BATCH] {label} lỗi, thử lại lần {attempt + 1}/3 sau 5 giây.", flush=True)
                time.sleep(5)
    raise RuntimeError(f"{label} thất bại sau 3 lần: {error}")


def priced_image(content: bytes, price: int) -> bytes:
    """Create the red, centred sale-price badge used only for Zalo returns."""
    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    label = format_price_badge(price)
    size = max(32, min(width, height) // 7)
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
    padding_x, padding_y = max(18, size // 3), max(12, size // 5)
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
    image.save(output, format="JPEG", quality=94, optimize=True)
    return output.getvalue()


@dataclass
class PendingBatch:
    sender_id: str
    sender_name: str
    images: list[str] = field(default_factory=list)
    price_text: str | None = None
    return_callback: Callable[[list[tuple[bytes, str]], dict[str, Any]], None] | None = None


class WebsiteBridge:
    def __init__(self, settings: dict[str, Any] | None):
        self.settings = settings or {}
        self.batches: dict[tuple[str, str], PendingBatch] = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)

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
        response = requests.get(
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

    def _upload_and_store(self, base: str, source_url: str, price: int, title: str, main_acc: str, owner: str | None) -> bytes:
        def download_and_render():
            response = requests.get(source_url, timeout=60)
            response.raise_for_status()
            # Keep the original bytes for the website.  The rendered copy is only
            # used for the album sent back through Zalo.
            return response.content, priced_image(response.content, price)

        original, rendered = retry(download_and_render, f"Ảnh {title}")

        def upload_image():
            response = requests.post(
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
        row = {"price": str(price), "description": DESCRIPTION, "image_url": image_url, "main_acc": main_acc}
        if owner:
            row["owner"] = owner
        existing = requests.get(f"{base}/accounts", params={"select": "id", "title": f"eq.{title}"}, headers=self._headers(), timeout=30)
        existing.raise_for_status()
        rows = existing.json()
        if rows:
            account_id = rows[0]["id"]
            updated = requests.patch(f"{base}/accounts", params={"id": f"eq.{account_id}"}, json=row, headers=self._headers(), timeout=30)
            updated.raise_for_status()
        else:
            created = requests.post(f"{base}/accounts", json={"title": title, **row}, headers=self._headers(), timeout=30)
            created.raise_for_status()
            account_id = created.json()[0]["id"]
            linked = requests.post(f"{base}/account_images", json={"acc_id": account_id, "image_url": image_url}, headers=self._headers(), timeout=30)
            linked.raise_for_status()
        return rendered

    def _import(self, batch: PendingBatch) -> None:
        required = ("supabase_url", "supabase_service_key", "cloudinary_cloud_name", "cloudinary_upload_preset")
        if any(not self.settings.get(key) for key in required):
            print("[BATCH] Thiếu cấu hình Supabase/Cloudinary trong website.js.", flush=True)
            return
        prices = [raised_price(value) for value in parse_prices(batch.price_text or "")]
        base = str(self.settings["supabase_url"]).rstrip("/") + "/rest/v1"
        main_acc = main_acc_from_text(batch.price_text or "", batch.sender_name)
        try:
            owner = self.resolve_account_owner(base)
        except requests.RequestException as error:
            print(f"[BATCH] Không đọc được owner nội bộ của web: {error}", flush=True)
            owner = None
        returned: list[tuple[bytes, str]] = []
        for position, (source_url, price) in enumerate(zip(batch.images, prices), start=1):
            digest = hashlib.sha256(f"{batch.sender_id}:{source_url}".encode()).hexdigest()[:12]
            title = f"fat_{digest}"
            try:
                rendered = self._upload_and_store(base, source_url, price, title, main_acc, owner)
                returned.append((rendered, f"{position:03d}.jpg"))
            except (KeyError, OSError, ValueError, requests.RequestException, RuntimeError) as error:
                print(f"[BATCH] Bỏ ảnh vị trí {position} (giá {format_vnd(price)}): {error}", flush=True)
        if not returned:
            print("[BATCH] Không ảnh nào được cập nhật, không trả album.", flush=True)
            return
        owner_status = "đã gán" if owner else "chưa tìm thấy"
        print(f"[BATCH] Đã cập nhật {len(returned)}/{len(prices)} account | Chủ acc: {main_acc} | Owner web: {owner_status}", flush=True)
        if batch.return_callback:
            batch.return_callback(returned, {
                "count": len(returned),
                "main_acc": main_acc,
                "sender_id": batch.sender_id,
                "sender_name": batch.sender_name,
            })
