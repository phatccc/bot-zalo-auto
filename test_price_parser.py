import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
import requests

from website_bridge import MISSING_ACCOUNT_PRICE, format_price_badge, image_signature, parse_prices, priced_image, raised_price, retry, signature_similarity


class PriceParserTests(unittest.TestCase):
    def test_real_price_list_with_provider_notes(self):
        text = """Chủ: Phạm Kính
Sản xuất dc full Ẹc không mũ đinh - Ae có. Khách hú nha
162 (Qt)
87 (Qt)
50 (Qt)
25 (VNG)
45 (VNG)
33 (VNG)
27 (VNG"""
        self.assertEqual(parse_prices(text), [162_000_000, 87_000_000, 50_000_000, 25_000_000, 45_000_000, 33_000_000, 27_000_000])

    def test_common_price_notations(self):
        self.assertEqual(parse_prices("2m8 - 3m - 850k"), [2_800_000, 3_000_000, 850_000])
        self.assertEqual(parse_prices("2.8m; 2,8m; 2tr8"), [2_800_000, 2_800_000, 2_800_000])
        self.assertEqual(parse_prices("Giá: 2 triệu\n2.800.000\n2,800,000"), [2_000_000, 2_800_000, 2_800_000])

    def test_price_grids_and_sale_qualifiers(self):
        bare_grid = """Nhà còn vài bé nhờ ae treo giùm em
18. 6.5. 3.5
10. 57. 9.5
2.5. 10.5. 6
10.5. 10. 16
14.5. 36. 14.5
14.5. 14. 68
5.5"""
        self.assertEqual(parse_prices(bare_grid), [
            18_000_000, 6_500_000, 3_500_000, 10_000_000, 57_000_000,
            9_500_000, 2_500_000, 10_500_000, 6_000_000, 10_500_000,
            10_000_000, 16_000_000, 14_500_000, 36_000_000, 14_500_000,
            14_500_000, 14_000_000, 68_000_000, 5_500_000,
        ])
        tagged_grid = """65m gct 62m gct 47m gct
35m gct 46m gct 45m gct
15 gct rip 10m gct 11m gct
5,8 gct rip 5 gct rip 17m5 gct
21 gct gg"""
        self.assertEqual(parse_prices(tagged_grid), [
            65_000_000, 62_000_000, 47_000_000, 35_000_000, 46_000_000,
            45_000_000, 15_000_000, 10_000_000, 11_000_000, 5_800_000,
            5_000_000, 17_500_000, 21_000_000,
        ])

    def test_numbered_and_annotated_lines(self):
        self.assertEqual(parse_prices("1. 2m8\n• 3m (full)\n# 850k [VNG]"), [2_800_000, 3_000_000, 850_000])

    def test_normal_chat_with_numbers_is_not_a_price_list(self):
        self.assertEqual(parse_prices("tìm 2 ẹc áo a hoặc b mũ đinh 20 quay 3x tìm quạ 7 ướp đinh ạ"), [])
        self.assertEqual(parse_prices("Tìm couple đen s1"), [])
        self.assertEqual(parse_prices("gửi ae ít acc nhờ ae treo hộ nha\nBao back 10% trong tháng nhé ae ơi"), [])

    def test_spaced_prices_currency_and_common_sale_notes(self):
        text = "2m 8 - 2tr 8 - 1 triệu 250 - 2m5đ - 3m vnd - 4m fix - 5m ib"
        self.assertEqual(parse_prices(text), [
            2_800_000, 2_800_000, 1_250_000, 2_500_000,
            3_000_000, 4_000_000, 5_000_000,
        ])

    def test_stray_bo_prefix_before_price_does_not_shift_positions(self):
        self.assertEqual(parse_prices("14.5 14 68\nBo. 5.5"), [
            14_500_000, 14_000_000, 68_000_000, 5_500_000,
        ])
        self.assertEqual(parse_prices("bỏ: 2m8\nbay"), [2_800_000, MISSING_ACCOUNT_PRICE])

    def test_compact_badge_price(self):
        self.assertEqual(format_price_badge(12_500_000), "12m5")
        self.assertEqual(format_price_badge(3_100_000), "3m1")
        self.assertEqual(format_price_badge(1_550_000), "1m55")

    def test_missing_account_placeholder_keeps_image_position(self):
        self.assertEqual(parse_prices("13m - 12 m - bay"), [13_000_000, 12_000_000, MISSING_ACCOUNT_PRICE])
        self.assertEqual(parse_prices("13m\nbay\n12m"), [13_000_000, MISSING_ACCOUNT_PRICE, 12_000_000])
        self.assertEqual(parse_prices("13m - sold - 14m\n12m - dabay - 15m"), [
            13_000_000, MISSING_ACCOUNT_PRICE, 14_000_000,
            12_000_000, MISSING_ACCOUNT_PRICE, 15_000_000,
        ])
        self.assertEqual(parse_prices("10m - đã bay - 11m\n12m - đã bán - 13m"), [
            10_000_000, MISSING_ACCOUNT_PRICE, 11_000_000,
            12_000_000, MISSING_ACCOUNT_PRICE, 13_000_000,
        ])
        self.assertEqual(parse_prices("nick bay rồi"), [])
        self.assertEqual(raised_price(MISSING_ACCOUNT_PRICE), MISSING_ACCOUNT_PRICE)
        self.assertEqual(format_price_badge(MISSING_ACCOUNT_PRICE), "999m")

    def test_return_image_has_a_centred_red_badge(self):
        source = io.BytesIO()
        Image.new("RGB", (600, 400), "black").save(source, format="JPEG")
        output = Image.open(io.BytesIO(priced_image(source.getvalue(), 12_500_000)))
        self.assertEqual(output.size, (600, 400))
        centre = output.crop((180, 140, 420, 260))
        self.assertTrue(any(red > 180 and green < 70 and blue < 70 for red, green, blue in centre.get_flattened_data()))

    def test_return_image_is_bounded_without_changing_small_images(self):
        source = io.BytesIO()
        Image.new("RGB", (2400, 1200), "black").save(source, format="JPEG")
        output = Image.open(io.BytesIO(priced_image(source.getvalue(), 12_500_000)))
        self.assertEqual(output.size, (1920, 960))

    def test_http_error_retries_three_times(self):
        response = requests.Response()
        response.status_code = 400
        attempts = []

        def bad_request():
            attempts.append(1)
            raise requests.HTTPError("bad request", response=response)

        with patch("website_bridge.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                retry(bad_request, "ảnh test")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_image_signature_survives_centred_price_badge(self):
        source = Image.new("RGB", (900, 600), "black")
        draw = ImageDraw.Draw(source)
        for offset in range(0, 900, 45):
            draw.rectangle((offset, 0, offset + 22, 599), fill=(offset % 255, (offset * 3) % 255, (offset * 7) % 255))
        raw = io.BytesIO()
        source.save(raw, format="JPEG", quality=92)
        original_signature = image_signature(raw.getvalue())
        labelled_signature = image_signature(priced_image(raw.getvalue(), 76_000_000))
        confidence = signature_similarity(labelled_signature, original_signature)
        self.assertIsNotNone(confidence)
        self.assertGreaterEqual(confidence, 90)

    def test_invalid_image_signature_never_matches(self):
        self.assertIsNone(signature_similarity("not-json", "also-not-json"))


if __name__ == "__main__":
    unittest.main()
