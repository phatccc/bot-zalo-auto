from munch import DefaultMunch
from dataclasses import dataclass
from typing import Any, List

class User(DefaultMunch):
	def __repr__(self):
		attrs = [f"{key}={value!r}" for key, value in self.__dict__.items()]
		return f"User({', '.join(attrs)})"


class Group(DefaultMunch):
	def __repr__(self):
		attrs = [f"{key}={value!r}" for key, value in self.__dict__.items()]
		return f"Group({', '.join(attrs)})"


class ContextObject(DefaultMunch):
	def __repr__(self):
		attrs = [f"{key}={value!r}" for key, value in self.__dict__.items()]
		return f"Context({', '.join(attrs)})"


class MessageObject(DefaultMunch):
	def __repr__(self):
		attrs = [f"{key}={value!r}" for key, value in self.__dict__.items()]
		return f"Message({', '.join(attrs)})"


@dataclass
class ImageGroup:
	"""A complete multi-image message, ordered as it appears in Zalo.

	``images`` contains the original content objects for each image. The matching
	raw ``MessageObject`` instances are passed separately to ``onMessage``.
	"""

	group_id: Any
	total: int
	images: List[Any]


@dataclass
class ImageSendFailure:
	"""A local image that could not be included in a multi-image send."""

	index: int
	path: str
	stage: str
	error: str


@dataclass
class MultiImageSendResult:
	"""Outcome of :meth:`ZaloAPI.sendMultiLocalImage`.

	``sent`` contains the responses returned by Zalo for images that were sent.
	``failed`` records images that could not be read, uploaded, or sent.
	"""

	sent: List[Any]
	failed: List[ImageSendFailure]


class EventObject(DefaultMunch):
	def __repr__(self):
		attrs = [f"{key}={value!r}" for key, value in self.__dict__.items()]
		return f"GroupEvent({', '.join(attrs)})"
