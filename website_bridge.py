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
# Status words occupy an image position even though the account is no longer
# available.  Treat them as the same 999m placeholder so later image/price
# pairs never shift (for example: `13m - sold - 14m`).
MISSING_ACCOUNT_TOKENS = {"bay", "sold", "dabay", "daban", "banroi", "out"}
# These are common sale qualifiers, not account-description words.  They are
# accepted only after a price-only line has otherwise been identified, keeping
# ordinary messages with numbers from starting a batch accidentally.
SALE_QUALIFIER_TOKENS = {
    "gct", "rip", "gg", "fix", "ib", "bao", "gop", "góp", "full",
    "vnd", "vnđ", "đ", "dong", "đồng",
}
MISSING_ACCOUNT_PRICE = 999_000_000


def normalise_missing_status(line: str) -> str:
    """Normalize multi-word unavailable statuses into a single slot token."""
    line = re.sub(r"\b(?:đã|da)\s*[-_.]?\s*bay\b", "bay", line, flags=re.I)
    line = re.sub(r"\b(?:đã|da)\s*[-_.]?\s*bán\b", "daban", line, flags=re.I)
    return re.sub(r"\bbán\s*[-_.]?\s*rồi\b|\bban\s*[-_.]?\s*roi\b", "banroi", line, flags=re.I)


def is_missing_status(segment: str) -> bool:
    value = normalise_missing_status(segment).strip(".,;:()[]{} ").casefold()
    return value in MISSING_ACCOUNT_TOKENS


def is_unknown_price_slot(segment: str) -> bool:
    """An arbitrary one-word status between price cells, such as `done`/`abc`."""
    value = segment.strip(".,;:()[]{} ")
    return bool(re.fullmatch(r"[A-Za-zÀ-ỹ]{1,24}", value))


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
        # Sellers use both Vietnamese and English status words between prices.
        # Normalize multi-word status labels before splitting the price row.
        line = normalise_missing_status(line)
        # Some sellers omit the final `)` in notes like `27 (VNG`; annotations
        # must not make that otherwise valid price disappear.
        line = re.sub(r"\([^)]*(?:\)|$)|\[[^]]*(?:\]|$)", " ", line).strip()
        # Optional `giá:` prefix and common list markers (`1. 2m8`, `• 2m8`).
        line = re.sub(r"^(?:giá|gia|price)\s*[:=-]?\s*", "", line, flags=re.I)
        # A few sellers prefix the last value with `bo.` / `bỏ:`.  When it is
        # immediately followed by a number it is only a stray label, not an
        # extra account position (`Bo. 5.5` must remain one 5.5m price).
        line = re.sub(r"^(?:bo|bỏ)\s*[.:=-]?\s*(?=\d)", "", line, flags=re.I)
        line = re.sub(r"^[#•*]\s*", "", line)
        # Do not mistake a real bare price such as `18. 6.5 3.5` for a list
        # index.  Numeric markers are removed only when the following price
        # has an explicit money unit (`1. 2m8`, `2) 850k`).
        line = re.sub(r"^\d+[.)]\s+(?=\d+(?:[.,]\d+)?(?:m\d*|k|tr\d*|triệu\d*|trieu\d*)\b)", "", line, flags=re.I)
        # Merge a deliberately spaced million fraction (`2m 8`, `2 tr 8`,
        # `1 triệu 250`) without merging separate values such as `2m 8m`.
        # The (?!\d) after \d{1,3} prevents backtracking: without it, the regex
        # would try '13' in `25m 13m`, see the lookahead fail (next char is 'm'),
        # then backtrack to match only '1' and pass the lookahead (next is '3m'),
        # producing the invalid token `25m13m`.
        line = re.sub(
            r"(\d+(?:[.,]\d+)?)\s*(?:m|tr|triệu|trieu)\.?\s+(\d{1,3})(?!\d)(?!\s*(?:m\d*|k|tr\d*|triệu\d*|trieu\d*)\b)",
            r"\1m\2",
            line,
            flags=re.I,
        )
        # Merge an isolated unit suffix written with a space (`2 m`, `3 k`) into
        # the preceding number.  The negative lookahead (?!\d) prevents stealing
        # the `m` from the *next* price token — e.g. `25m 13m` must stay two
        # separate values, not collapse to the unrecognised token `25m13m`.
        line = re.sub(r"(\d+(?:[.,]\d+)?)\s+(m|k)\b(?!\d)", r"\1\2", line, flags=re.I)
        line = re.sub(r"(\d+(?:[.,]\d+)?)\s*(?:triệu|trieu|tr)\b", r"\1tr", line, flags=re.I)
        # Currency written immediately after the number is cosmetic: `2m5đ`.
        line = re.sub(r"(?<=\d)(?:vnđ|vnd|đồng|dong|đ)\b", "", line, flags=re.I)
        tokens = re.sub(r"\s*[-|;/]\s*", " ", line).split()
        normalized = [token.strip(".,;:()[]{}").lower() for token in tokens]
        price_tokens = [token for token in normalized if PRICE_TOKEN.fullmatch(token) or token in MISSING_ACCOUNT_TOKENS]
        qualifiers = [token for token in normalized if token not in price_tokens]
        is_plain_price_line = tokens and len(price_tokens) == len(normalized)
        # Sellers commonly append `gct`, `rip` or `gg`, for example
        # `65m gct` / `15 gct rip`.  Those suffixes do not change the price.
        is_qualified_price_line = price_tokens and qualifiers and all(token in SALE_QUALIFIER_TOKENS for token in qualifiers)
        if is_plain_price_line or is_qualified_price_line:
            raw_tokens.extend(price_tokens)
            has_real_price = has_real_price or any(PRICE_TOKEN.fullmatch(token) for token in price_tokens)
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


def parse_price_slots_with_unknowns(text: str) -> tuple[list[int | None], list[str]]:
    """Keep one unknown, delimiter-separated word as a candidate price slot.

    Unknown words are never made into prices here.  They become a placeholder
    only later, when the resulting slot count exactly equals the album count.
    """
    slots: list[int | None] = []
    unknown_labels: list[str] = []
    for source_line in price_text_without_owner(text).splitlines():
        strict = parse_prices(source_line)
        if strict:
            slots.extend(strict)
            continue
        if not re.search(r"[-|;/]", source_line):
            continue
        line_slots: list[int | None] = []
        line_unknowns: list[str] = []
        valid_line = True
        real_price_count = 0
        for segment in re.split(r"\s*[-|;/]\s*", source_line):
            segment = segment.strip()
            if not segment:
                valid_line = False
                break
            values = parse_prices(segment)
            if values:
                line_slots.extend(values)
                real_price_count += sum(value != MISSING_ACCOUNT_PRICE for value in values)
            elif is_missing_status(segment):
                line_slots.append(MISSING_ACCOUNT_PRICE)
            elif is_unknown_price_slot(segment):
                line_slots.append(None)
                line_unknowns.append(segment.strip(".,;:()[]{} "))
            else:
                valid_line = False
                break
        # A line must contain at least one actual number.  This rejects normal
        # prose such as `gửi - ae - xem` before it can become a fake batch.
        if valid_line and real_price_count and line_unknowns:
            slots.extend(line_slots)
            unknown_labels.extend(line_unknowns)
    return slots, unknown_labels


def prices_for_image_count(text: str, image_count: int) -> tuple[list[int], list[str]]:
    """Resolve arbitrary status words only if they make the album exact."""
    strict = parse_prices(text)
    slots, unknown_labels = parse_price_slots_with_unknowns(text)
    if unknown_labels and len(slots) == image_count:
        return [MISSING_ACCOUNT_PRICE if value is None else value for value in slots], unknown_labels
    return strict, []


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


def retry(action: Callable[[], Any], label: str, max_attempts: int = 4) -> Any:
    """Retry mọi thao tác ảnh tối đa max_attempts lần với back-off tăng dần.

    Khoảng chờ: lần 2 → 5 s, lần 3 → 15 s, lần 4 → 20 s.
    Bắt thêm RuntimeError để nested retry không bị wrap.
    """
    error = None
    delays = [5, 15, 20]
    for attempt in range(1, max_attempts + 1):
        try:
            return action()
        except (OSError, ValueError, RuntimeError, requests.RequestException) as caught:
            error = caught
            if attempt < max_attempts:
                wait = delays[min(attempt - 1, len(delays) - 1)]
                print(f"[BATCH] {label} lỗi ({caught.__class__.__name__}), thử lại lần {attempt + 1}/{max_attempts} sau {wait} giây.", flush=True)
                time.sleep(wait)
                continue
            break
    raise RuntimeError(f"{label} thất bại sau {max_attempts} lần: {error}")


def verified_image_content(content: bytes) -> bytes:
    """Reject empty/truncated CDN responses before they reach Cloudinary.

    Zalo's CDN can occasionally answer a successful HTTP request with an empty
    body. Cloudinary then reports ``400: Empty file`` and retrying that same
    byte string can never succeed. Verifying it here makes the *download*
    retry instead, while keeping the image-to-price mapping unchanged.
    """
    if not content:
        raise ValueError("Dữ liệu ảnh tải về rỗng")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    except (OSError, ValueError) as error:
        raise ValueError("Dữ liệu tải về không phải ảnh hợp lệ") from error
    return content


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
    """Hash four border regions, intentionally excluding the centred price badge.

    Falls back to a plain SHA-256 hex when the bytes cannot be decoded as an
    image (e.g. truncated download), so the account row is still written.
    """
    try:
        with Image.open(io.BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except Exception:
        return json.dumps({"v": 0, "sha256": hashlib.sha256(content).hexdigest()}, separators=(",", ":"))
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
    resolved_prices: list[int] = field(default_factory=list)
    auto_placeholder_labels: list[str] = field(default_factory=list)
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
        # Tracks batches currently being imported to prevent duplicate submissions
        # caused by Zalo firing the same album event twice.
        self.processing_batches: set[tuple[str, str]] = set()

    def _http(self) -> requests.Session:
        """One persistent connection pool per worker thread."""
        session = getattr(self._http_local, "session", None)
        if session is None:
            session = requests.Session()
            self._http_local.session = session
        return session

    def receive(self, mid, author_id, message, details, thread_id, thread_type, return_callback=None) -> None:
        urls = image_urls(message)
        if isinstance(message, str):
            strict_prices = parse_prices(message)
            candidate_slots, _ = parse_price_slots_with_unknowns(message)
            is_prices = bool(strict_prices or candidate_slots)
        else:
            is_prices = False
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
            prices, placeholder_labels = prices_for_image_count(batch.price_text or "", len(batch.images))
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
            if key in self.processing_batches:
                print(f"[BATCH] {key[0]}: batch đang được xử lý, bỏ qua lần gửi trùng.", flush=True)
                return
            batch.resolved_prices = prices
            batch.auto_placeholder_labels = placeholder_labels
            if placeholder_labels:
                print(f"[BATCH] Tự giữ {len(placeholder_labels)} vị trí chữ lạ thành 999m: {', '.join(placeholder_labels)}", flush=True)
            batch = self.batches.pop(key)
            self.processing_batches.add(key)
        self.executor.submit(self._import, batch, key)

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

    def _upload_and_store(self, base: str, source_url: str, price: int, title: str, main_acc: str, owner: str | None, batch_id: str, position: int, existing_id: str | None, lookup_per_item: bool, prefetched_content: bytes | None = None) -> bytes:
        session = self._http()

        if prefetched_content is not None:
            try:
                original = verified_image_content(prefetched_content)
                self.progress.update_item(batch_id, position, "Đang dán giá")
            except ValueError:
                # A malformed prefetch must not be uploaded repeatedly. Fetch
                # it again below so retry obtains fresh CDN bytes.
                prefetched_content = None
        else:
            original = None

        if prefetched_content is None:
            def download_original():
                self.progress.update_item(batch_id, position, "Đang tải và dán giá")
                response = session.get(source_url, timeout=60)
                response.raise_for_status()
                return verified_image_content(response.content)

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
                # 180 s covers large 4K images on a slow VPS connection.
                timeout=180,
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

    def _import(self, batch: PendingBatch, key: tuple[str, str] | None = None) -> None:
        required = ("supabase_url", "supabase_service_key", "cloudinary_cloud_name", "cloudinary_upload_preset")
        if any(not self.settings.get(k) for k in required):
            print("[BATCH] Thiếu cấu hình Supabase/Cloudinary trong website.js.", flush=True)
            self.progress.record_issue("Thiếu cấu hình website", "Không thể cập nhật list vì website.js thiếu Supabase hoặc Cloudinary.")
            if key is not None:
                self.processing_batches.discard(key)
            return
        raw_prices = batch.resolved_prices or parse_prices(batch.price_text or "")
        prices = [raised_price(value) for value in raw_prices]
        base = str(self.settings["supabase_url"]).rstrip("/") + "/rest/v1"
        main_acc = main_acc_from_text(batch.price_text or "", batch.sender_name)
        batch_id = self.progress.start(len(prices), batch.sender_name, main_acc)
        if batch.auto_placeholder_labels:
            self.progress.record_issue(
                "Tự thay ô chữ lạ thành 999m",
                f"Đã giữ đúng thứ tự ảnh bằng cách thay: {', '.join(batch.auto_placeholder_labels)}. Chỉ áp dụng vì tổng số vị trí khớp đúng số ảnh.",
                batch_id=batch_id,
                severity="warning",
            )
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
        # Pre-download tất cả ảnh ngay lập tức — URL Zalo CDN có thể hết hạn
        # trong lúc các worker trước đang upload Cloudinary. Tải trước đảm bảo
        # mọi ảnh đều được lấy khi token còn hiệu lực.
        self.progress.update_batch(batch_id, f"Đang tải trước {len(tasks)} ảnh")
        prefetched: dict[str, bytes] = {}
        with ThreadPoolExecutor(max_workers=min(len(tasks), 10)) as dl_pool:
            def _fetch(url: str) -> tuple[str, bytes]:
                session = self._http()
                def _get():
                    r = session.get(url, timeout=60)
                    r.raise_for_status()
                    return verified_image_content(r.content)
                return url, retry(_get, f"Prefetch {url.split('/')[-1][:20]}")
            dl_futures = {dl_pool.submit(_fetch, source_url): position for position, source_url, price, title in tasks}
            for dl_future in as_completed(dl_futures):
                pos = dl_futures[dl_future]
                try:
                    url, content = dl_future.result()
                    prefetched[url] = content
                    self.progress.update_item(batch_id, pos, "Đã tải")
                except (OSError, ValueError, requests.RequestException, RuntimeError) as dl_err:
                    self.progress.update_item(batch_id, pos, "Lỗi tải trước", str(dl_err))
                    print(f"[BATCH] Prefetch ảnh {pos} thất bại: {dl_err}", flush=True)

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
                    prefetched.get(source_url),
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
        if key is not None:
            self.processing_batches.discard(key)
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
