# -*- coding: UTF-8 -*-

import time, datetime
import urllib.parse, json
import gzip, base64, zlib

from . import _exception
from Crypto.Cipher import AES
from ._aevents import GroupEventType

#: Default headers
HEADERS = {
	"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Accept": "application/json, text/plain, */*",
	"sec-ch-ua": "\"Not-A.Brand\";v=\"99\", \"Chromium\";v=\"124\"",
	"sec-ch-ua-mobile": "?0",
	"sec-ch-ua-platform": "\"Linux\"",
	"origin": "https://chat.zalo.me",
	"sec-fetch-site": "same-site",
	"sec-fetch-mode": "cors",
	"sec-fetch-dest": "empty",
	"Accept-Encoding": "gzip",
	"referer": "https://chat.zalo.me/",
	"accept-language": "vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5",
}

#: Default cookies
COOKIES = {}


def now():
	return int(time.time() * 1000)

def formatTime(format, ftime=None):
	if ftime is None:
		ftime = now()
	dt = datetime.datetime.fromtimestamp(ftime / 1000)
	# vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
	# dt_vietnam = vietnam_tz.fromutc(dt)

	formatted_time = dt.strftime(format)

	return formatted_time


def getHeader(buffer):
	if len(buffer) < 4:
		raise ValueError("Invalid header")

	return [buffer[0], int.from_bytes(buffer[1:3], "little"), buffer[3]]


CLIENT_MESSAGE_TYPES = {
	"webchat": 1,
	"chat.voice": 31,
	"chat.photo": 32,
	"chat.sticker": 36,
	"chat.doodle": 37,
	"chat.recommended": 38,
	"chat.link": 38,
	"chat.location.new": 43,
	"chat.video.msg": 44,
	"share.file": 46,
	"chat.gif": 49,
}


def getClientMessageType(msgType):
	return CLIENT_MESSAGE_TYPES.get(msgType, 1)


GROUP_EVENT_TYPES = {
	"join_request": GroupEventType.JOIN_REQUEST,
	"join": GroupEventType.JOIN,
	"leave": GroupEventType.LEAVE,
	"remove_member": GroupEventType.REMOVE_MEMBER,
	"block_member": GroupEventType.BLOCK_MEMBER,
	"update_setting": GroupEventType.UPDATE_SETTING,
	"update": GroupEventType.UPDATE,
	"new_link": GroupEventType.NEW_LINK,
	"add_admin": GroupEventType.ADD_ADMIN,
	"remove_admin": GroupEventType.REMOVE_ADMIN,
	"new_pin_topic": GroupEventType.NEW_PIN_TOPIC,
	"update_pin_topic": GroupEventType.UPDATE_PIN_TOPIC,
	"update_topic": GroupEventType.UPDATE_TOPIC,
	"update_board": GroupEventType.UPDATE_BOARD,
	"remove_board": GroupEventType.REMOVE_BOARD,
	"reorder_pin_topic": GroupEventType.REORDER_PIN_TOPIC,
	"unpin_topic": GroupEventType.UNPIN_TOPIC,
	"remove_topic": GroupEventType.REMOVE_TOPIC,
}


def getGroupEventType(act):
	return GROUP_EVENT_TYPES.get(act, GroupEventType.UNKNOWN)


def dict_to_raw_cookies(cookies_dict):
	try:
		cookie_string = "; ".join(f"{key}={value}" for key, value in cookies_dict.items())
		if not cookie_string:
			return None

		return cookie_string

	except:
		return None


def _pad(s, block_size):
	padding_length = block_size - len(s) % block_size

	return s + bytes([padding_length]) * padding_length


def _unpad(s, block_size):
	padding_length = s[-1]

	return s[:-padding_length]


def zalo_encode(params, key):
	try:
		key = base64.b64decode(key)
		iv = bytes.fromhex("00000000000000000000000000000000")
		cipher = AES.new(key, AES.MODE_CBC, iv)
		plaintext = json.dumps(params).encode()
		padded_plaintext = _pad(plaintext, AES.block_size)
		ciphertext = cipher.encrypt(padded_plaintext)

		return base64.b64encode(ciphertext).decode()

	except Exception as e:
		raise _exception.EncodePayloadError(f"Unable to encode payload! Error: {e}")


def zalo_decode(params, key):
	try:
		params = urllib.parse.unquote(params)
		key = base64.b64decode(key)
		iv = bytes.fromhex("00000000000000000000000000000000")
		cipher = AES.new(key, AES.MODE_CBC, iv)
		ciphertext = base64.b64decode(params.encode())
		padded_plaintext = cipher.decrypt(ciphertext)
		plaintext = _unpad(padded_plaintext, AES.block_size)
		plaintext = plaintext.decode("utf-8")

		if isinstance(plaintext, str):
			plaintext = json.loads(plaintext)

		return plaintext

	except Exception as e:
		raise _exception.DecodePayloadError(f"Unable to decode payload! Error: {e}")


def zws_decode(parsed, key):
	payload = parsed.get("data")
	encrypt_type = parsed.get("encrypt")
	if not payload or not key:
		return

	try:
		if encrypt_type == 0:

			decoded_data = payload

		elif encrypt_type == 1:

			decrypted_data = base64.b64decode(payload)
			decompressed_data = gzip.decompress(decrypted_data)
			decoded_data = decompressed_data.decode("utf-8")

		elif encrypt_type == 2:

			data_bytes = base64.b64decode(urllib.parse.unquote(payload))
			if len(data_bytes) >= 48:

				iv = data_bytes[:16]
				additional_data = data_bytes[16:32]
				data_source = data_bytes[32:]
				decryptor = AES.new(base64.b64decode(key), AES.MODE_GCM, nonce=iv)
				decryptor.update(additional_data)
				decrypted_data = decryptor.decrypt(data_source)[:-16]
				decompressed_data = zlib.decompress(decrypted_data, wbits=16)
				decoded_data = decompressed_data.decode("utf-8")

		else:

			decoded_data = None

		if not decoded_data:
			return

		return json.loads(decoded_data)

	except Exception as e:
		# return
		raise _exception.DecodePayloadError(f"Unable to decode payload! Error: {e}")
