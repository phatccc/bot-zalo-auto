import json
import tempfile
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import requests
from zlapi import ImageGroup, ZaloAPI
from zlapi.models import Message, ThreadType
from website_bridge import WebsiteBridge
from progress_dashboard import ProgressTracker, start_dashboard


def as_mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def event_summary(message, message_object):
    """Return a compact, human-readable representation of a Zalo event."""
    details = as_mapping(message_object)
    content = as_mapping(message)
    message_type = details.get("msgType", "unknown")

    if message_type == "chat.reaction":
        reaction = content.get("rIcon", "(không rõ)")
        target = next(iter(content.get("rMsg", [])), {})
        return {
            "event": "Phản ứng",
            "content": reaction,
            "reply_to_message_id": target.get("gMsgID"),
        }

    if message_type == "chat.recommended":
        return {
            "event": "Thông báo/gợi ý",
            "content": content.get("description") or content.get("title") or "(không có nội dung)",
            "action": content.get("action"),
        }

    if message_type == "chat.photo":
        return {
            "event": "Ảnh",
            "caption": content.get("title") or "(không có chú thích)",
            "url": content.get("href"),
        }

    if isinstance(message, ImageGroup):
        return {
            "event": "Album ảnh",
            "image_count": len(message.images),
            "group_id": str(message.group_id),
        }

    if isinstance(message, str):
        return {"event": "Tin nhắn", "content": message}

    return {
        "event": "Sự kiện khác",
        "content": content or str(message),
    }


def format_time(timestamp):
    try:
        return datetime.fromtimestamp(int(timestamp) / 1000).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


class JsonLoggerClient(ZaloAPI):
    """Zalo client that logs concise incoming events as formatted JSON."""

    def __init__(self, imei, cookies, website, progress, return_images=True):
        super().__init__(imei=imei, cookies=cookies)
        self.progress = progress
        self.website_bridge = WebsiteBridge(website, progress)
        self.return_images = return_images
        self.return_executor = ThreadPoolExecutor(max_workers=1)
        self.returned_groups = set()
        self.returning_threads = {}

    def return_image_group(self, author_id, group_id, images, thread_id, thread_type, notification=None):
        """Send price-labelled images only after their web update succeeded."""
        group_key = (str(thread_id), str(author_id), str(group_id))
        now = time.monotonic()
        pending_return = self.returning_threads.get(str(thread_id))
        if group_key in self.returned_groups or str(author_id) == str(self.user_id) or (pending_return and pending_return[0] > now):
            return
        self.returned_groups.add(group_key)
        self.return_executor.submit(
            self._download_and_return_group,
            group_key,
            images,
            thread_id,
            thread_type,
            notification,
        )

    def _download_and_return_group(self, group_key, images, thread_id, thread_type, notification):
        try:
            with tempfile.TemporaryDirectory(prefix="zalo-return-") as directory:
                paths = []
                for index, (content, filename) in enumerate(images, start=1):
                    extension = Path(filename).suffix or ".jpg"
                    path = Path(directory) / f"{index:03d}{extension}"
                    path.write_bytes(content)
                    paths.append(str(path))
                self.sendMultiLocalImage(paths, thread_id, thread_type)
                # Zalo sẽ gửi event cho chính album vừa trả. Bỏ qua nó để tránh
                # tạo một vòng trả ảnh lặp vô hạn.
                self.returning_threads[str(thread_id)] = (time.monotonic() + 30, len(images))
                if notification:
                    self.send(Message(text=notification), thread_id, thread_type)
            print(f"[RETURN] Đã gửi album {len(images)} ảnh đã dán giá tới {thread_id}.", flush=True)
        except Exception as error:
            self.returned_groups.discard(group_key)
            print(f"[RETURN] Không thể gửi lại album: {error}", flush=True)

    def resolve_sender_name(self, author_id, details):
        """Zalo image events often omit dName; resolve it before creating a batch."""
        name = details.get("dName") or details.get("displayName") or details.get("zaloName")
        if name:
            return str(name)
        try:
            profile_data = as_mapping(self.fetchUserInfo(author_id))
            profiles = as_mapping(profile_data.get("changed_profiles") or profile_data.get("profiles"))
            profile = as_mapping(profiles.get(str(author_id)))
            return str(profile.get("zaloName") or profile.get("displayName") or profile.get("name") or "(không rõ)")
        except Exception:
            return "(không rõ)"

    def onMessage(self, mid, author_id, message, message_object, thread_id, thread_type):
        pending_return = self.returning_threads.get(str(thread_id))
        if isinstance(message, ImageGroup) and pending_return and pending_return[0] > time.monotonic() and len(message.images) == pending_return[1]:
            # Websocket echo of the album this bot just returned, not a new batch.
            self.returning_threads.pop(str(thread_id), None)
            print("[RETURN] Bỏ qua event album do bot vừa gửi.", flush=True)
            return
        details = as_mapping(message_object)
        sender_name = self.resolve_sender_name(author_id, details)
        details["dName"] = sender_name
        event = {
            "time": format_time(details.get("ts")),
            "message_id": mid,
            "message_type": details.get("msgType", "unknown"),
            "sender": {
                "name": sender_name,
                "id": author_id,
            },
            "conversation": {
                "type": str(thread_type).removeprefix("ThreadType."),
                "id": thread_id,
            },
            **event_summary(message, message_object),
        }
        print(json.dumps(event, ensure_ascii=False, indent=2, default=str), flush=True)
        print()
        return_callback = None
        if self.return_images and isinstance(message, ImageGroup):
            def return_callback(images, result):
                # Lists can come from anyone in a group, but confirmations and the
                # labelled album always go to the bot owner in a private chat.
                destination = str(self.website_bridge.settings.get("notification_chat_id") or "").strip()
                if not destination:
                    print("[RETURN] Thiếu notification_chat_id trong website.js; không gửi album riêng.", flush=True)
                    return
                notification = (
                    "✅ Đã cập nhật list lên web thành công.\n"
                    f"Chủ acc: {result['main_acc']}\n"
                    f"Người gửi list: {result['sender_name']} ({result['sender_id']})\n"
                    f"Số acc: {result['count']}"
                )
                self.return_image_group(
                    author_id,
                    message.group_id,
                    images,
                    destination,
                    ThreadType.USER,
                    notification,
                )
        self.website_bridge.receive(mid, author_id, message, details, thread_id, thread_type, return_callback)


def load_config():
    """Load IMEI and cookies from config.js, which uses JSON format."""
    config_path = Path(__file__).with_name("config.js")
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError as error:
        raise RuntimeError(f"Không tìm thấy file cấu hình: {config_path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"config.js không phải JSON hợp lệ: {error}") from error

    required_keys = ("imei", "cookie_name")
    missing_keys = [key for key in required_keys if key not in config]
    if missing_keys:
        raise RuntimeError(f"config.js thiếu trường: {', '.join(missing_keys)}")
    if not isinstance(config["cookie_name"], dict):
        raise RuntimeError("cookie_name trong config.js phải là một object JSON.")

    return config


def load_website_config():
    """Load the bot's direct Cloudinary/Supabase settings."""
    config_path = Path(__file__).with_name("website.js")
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            settings = json.load(config_file)
    except FileNotFoundError as error:
        raise RuntimeError(f"Không tìm thấy cấu hình website: {config_path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"website.js không phải JSON hợp lệ: {error}") from error

    required = ("supabase_url", "supabase_service_key", "cloudinary_cloud_name", "cloudinary_upload_preset")
    missing = [name for name in required if not isinstance(settings.get(name), str) or not settings[name].strip()]
    if missing:
        raise RuntimeError("website.js thiếu cấu hình: " + ", ".join(missing))
    return settings


def main():
    config = load_config()
    website = load_website_config()
    progress = ProgressTracker()
    dashboard_url = start_dashboard(progress, website)
    if dashboard_url:
        print(f"[DASHBOARD] Đang chạy tại {dashboard_url}", flush=True)
    client = JsonLoggerClient(
        imei=config["imei"],
        cookies=config["cookie_name"],
        website=website,
        progress=progress,
        return_images=config.get("return_images", True),
    )
    client.listen()


if __name__ == "__main__":
    main()
