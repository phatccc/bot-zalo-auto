---
name: zalo-batch-import
description: Maintain the Zalo image-and-price batch importer in this repository. Use when changing Zalo intake, price pairing, owner detection, website import, or image return behavior; do not use for unrelated Zalo commands.
---

# Zalo Batch Import

Work only in this bot repository unless the user explicitly authorizes a change to the website project.

## Batch contract

- A batch is eligible only when one sender in one conversation has an exact one-to-one pairing of at least two images and two prices. Do not guess or shift a price to another image.
- Preserve source-image order. If a specific image cannot be downloaded, decoded, uploaded, or stored after three attempts separated by five seconds, skip that position and retain the price mapping for the remaining positions.
- Persist the original image URL to the website. Render a separate price-labelled copy solely for the album returned through Zalo.
- Return the album only after each returned image has been stored successfully on the website. Never return an album when every image in the batch failed.

## Ownership and events

- Save the displayed account owner in `main_acc`: first honor a line such as `chủ: Tên` or `tên: Tên`; otherwise resolve and use the Zalo sender's display name.
- The `owner` database field is the website's internal user ID, not a Zalo ID. Prefer configured `account_owner`; otherwise resolve it from an existing website account.
- Suppress the websocket echo for an album sent by this bot so it cannot become a new batch. Keep terminal logs compact; do not print full image URLs or cookie/config values.

## Safety and verification

- Keep secrets only in local configuration files and never print them.
- Before finishing changes, compile `bot.py` and `website_bridge.py` with the repository virtual environment and test price parsing, owner extraction, and image rendering locally. Do not start a live bot or write to production merely to test unless the user requests it.
