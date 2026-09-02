# Zalo batch importer

Bot nhận nhóm ảnh và bảng giá từ Zalo, lưu ảnh gốc lên website, rồi gửi riêng album ảnh đã dán giá cho `notification_chat_id` sau khi cập nhật thành công.

## Cài đặt

Yêu cầu Python 3.11 hoặc mới hơn.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Trên Windows, thay lệnh activate bằng `.venv\Scripts\Activate.ps1`.

## Cấu hình riêng tư

Các file sau bị gitignore và **không được commit**:

- `config.js`: IMEI và cookie Zalo.
- `website.js`: Supabase service key, Cloudinary upload preset, ID Zalo nhận thông báo.

Tạo `config.js`:

```json
{
  "imei": "YOUR_IMEI",
  "cookie_name": { "cookie_name": "cookie_value" }
}
```

Tạo `website.js` từ `website-config.example.json`, sau đó điền các giá trị thật. `notification_chat_id` là Zalo ID duy nhất nhận album ảnh đã dán giá và thông báo thành công.

## Tốc độ và trang theo dõi

Bot xử lý tối đa 4 ảnh song song mặc định, vẫn ghép ảnh với giá và trả album
theo đúng thứ tự. Có thể chỉnh `"batch_workers"` từ `1` đến `5` trong
`website.js`; VPS 2 vCore nên giữ `4` để nhanh nhưng tránh làm Cloudinary/Zalo
quá tải.

Khi chạy, bot mở dashboard tại cổng `8787`. Từ máy khác, vào:

```text
http://IP_PUBLIC_CUA_VPS:8787
```

Dashboard chỉ hiện tiến độ, chủ acc, số lượng ảnh và lỗi; không chứa IMEI,
cookie, service key hoặc URL ảnh. Nếu VPS dùng UFW, mở cổng một lần:

```bash
sudo ufw allow 8787/tcp
```

## Quy tắc batch

- Cần số ảnh và số giá bằng nhau, tối thiểu 2.
- Chủ acc ưu tiên `chủ: Tên` hoặc `tên: Tên`; nếu thiếu, dùng tên Zalo người gửi list.
- Giá `bay` được hiểu là `999m` để giữ đúng vị trí ảnh.
- Website chỉ lưu ảnh gốc. Ảnh gửi lại Zalo mới được dán nhãn giá.

## Kiểm tra

```bash
python -m unittest -v test_price_parser.py
```
