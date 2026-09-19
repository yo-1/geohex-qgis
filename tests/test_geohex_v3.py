"""geohex_v3（公式 hex_v3.2_core.js の移植）の単体テスト。

期待値の出典:
- uupaa/GeoHex (TypeScript版 v3.2) の README に記載されたサンプル出力
- geohex_v3 (Ruby gem) の README に記載されたサンプル出力（西半球）
- Geo::Hex::V3::XS (Perl) のドキュメントに並記されたコードと座標
これらは公式JSと同一アルゴリズムの別実装の出力であり、公式JS自体での再検証は未実施。
"""

import math
import random
import unittest

from geohex_generator import geohex_v3 as gh


class KnownVectorTest(unittest.TestCase):
    def test_tokyo_area_level9_matches_uupaa_readme(self):
        zone = gh.get_zone_by_location(35.780516755235475, 139.57031250000003, 9)
        self.assertEqual(zone.code, "XM566370240")
        self.assertEqual((zone.x, zone.y), (101375, -35983))
        self.assertAlmostEqual(zone.lat, 35.78044332128247, places=10)
        self.assertAlmostEqual(zone.lon, 139.57018747142203, places=10)

    def test_western_hemisphere_level11_matches_ruby_readme(self):
        zone = gh.get_zone_by_location(33.127120, -117.3274073, 11)
        self.assertEqual(zone.code, "PC22751337146")
        self.assertEqual((zone.x, zone.y), (-250028, 789182))
        self.assertAlmostEqual(zone.lat, 33.127103696872936, places=10)
        self.assertAlmostEqual(zone.lon, -117.32741734265892, places=10)

    def test_level9_matches_perl_doc_pair(self):
        self.assertEqual(gh.get_zone_by_location(35.579826, 139.654524, 9).code, "XM488276746")

    def test_decode_returns_same_cell(self):
        zone = gh.get_zone_by_code("XM566370240")
        self.assertEqual((zone.x, zone.y, zone.level), (101375, -35983, 9))


class BasicPropertyTest(unittest.TestCase):
    def test_code_length_is_level_plus_two(self):
        for level in range(gh.MIN_LEVEL, gh.MAX_LEVEL + 1):
            self.assertEqual(len(gh.get_zone_by_location(35.0, 135.0, level).code), level + 2)

    def test_hex_size_shrinks_by_three_per_level(self):
        for level in range(gh.MIN_LEVEL, gh.MAX_LEVEL):
            self.assertAlmostEqual(gh.hex_size(level) / gh.hex_size(level + 1), 3.0, places=9)
        self.assertAlmostEqual(gh.hex_size(0), gh.H_BASE / 27)

    def test_js_round_rounds_half_up(self):
        self.assertEqual(gh._js_round(2.5), 3)
        self.assertEqual(gh._js_round(-2.5), -2)
        self.assertEqual(gh._js_round(3.5), 4)

    def test_random_points_roundtrip(self):
        rng = random.Random(20260919)
        for _ in range(3000):
            level = rng.randint(0, 15)
            lat = rng.uniform(20.0, 46.0)  # 日本周辺
            lon = rng.uniform(122.0, 154.0)
            zone = gh.get_zone_by_location(lat, lon, level)
            decoded = gh.get_zone_by_code(zone.code)
            self.assertEqual((decoded.x, decoded.y), (zone.x, zone.y))
            # セル中心を再度符号化すると同じセルになる
            self.assertEqual(gh.get_xy_by_location(zone.lat, zone.lon, level), (zone.x, zone.y))

    def test_random_points_roundtrip_world(self):
        rng = random.Random(1)
        for _ in range(3000):
            level = rng.randint(0, 12)
            lat = rng.uniform(-80.0, 80.0)
            lon = rng.uniform(-179.9, 179.9)
            zone = gh.get_zone_by_location(lat, lon, level)
            decoded = gh.get_zone_by_code(zone.code)
            self.assertEqual((decoded.x, decoded.y), (zone.x, zone.y), zone.code)

    def test_japan_region_is_bijective_at_low_levels(self):
        """日本周辺の全セルで コード⇔(x, y) が1対1であること（補正ロジックの整合性）。"""
        for level in range(0, 5):
            h = gh.hex_size(level)
            step_u, step_v = 3 * h, math.sqrt(3) * h
            x0, y0 = gh.loc2xy(122, 20)
            x1, y1 = gh.loc2xy(155, 46)
            codes = {}
            for u in range(math.ceil(x0 / step_u), math.floor(x1 / step_u) + 1):
                for v in range(math.ceil(y0 / step_v), math.floor(y1 / step_v) + 1):
                    if (u + v) & 1:
                        continue
                    xy = ((u + v) // 2, (v - u) // 2)
                    zone = gh.get_zone_by_xy(xy[0], xy[1], level)
                    self.assertNotIn(zone.code, codes, f"コード重複: {zone.code}")
                    codes[zone.code] = xy
                    self.assertEqual(gh.get_xy_by_code(zone.code), xy, zone.code)


class InvalidInputTest(unittest.TestCase):
    def test_invalid_level(self):
        for level in (-1, 16):
            with self.assertRaises(ValueError):
                gh.get_zone_by_location(35.0, 139.0, level)

    def test_invalid_latitude_longitude(self):
        for lat, lon in ((90, 0), (-90, 0), (91, 0), (35, 181), (35, -181)):
            with self.assertRaises(ValueError):
                gh.get_zone_by_location(lat, lon, 5)

    def test_invalid_code(self):
        for code in ("", "X", "1M123", "XM12A", "XM9", "XM" + "0" * 16, None):
            with self.assertRaises(ValueError, msg=repr(code)):
                gh.get_zone_by_code(code)


class PolygonTest(unittest.TestCase):
    def test_ring_is_closed_regular_hexagon_in_mercator(self):
        level = 6
        zone = gh.get_zone_by_location(35.68, 139.76, level)
        ring = gh.cell_polygon_lonlat(zone.x, zone.y, level)
        self.assertEqual(len(ring), 7)
        self.assertEqual(ring[0], ring[-1])

        cx, cy = gh.cell_center_mercator(zone.x, zone.y, level)
        size = gh.hex_size(level)
        for lon, lat in ring[:-1]:
            vx, vy = gh.loc2xy(lon, lat)
            self.assertAlmostEqual(math.hypot(vx - cx, vy - cy), 2 * size, delta=size * 1e-7)

    def test_ring_matches_official_get_hex_coords_formula(self):
        """公式JSの getHexCoords（中心経緯度を経由する計算）と一致すること。"""
        level = 7
        zone = gh.get_zone_by_location(43.06, 141.35, level)
        h_x, h_y = gh.loc2xy(zone.lon, zone.lat)
        size = gh.hex_size(level)
        dy = math.tan(math.pi * (60 / 180)) * size
        expected = [
            (gh.xy2loc(h_x - 2 * size, h_y)[0], zone.lat),
            (gh.xy2loc(h_x - size, h_y)[0], gh.xy2loc(h_x, h_y + dy)[1]),
            (gh.xy2loc(h_x + size, h_y)[0], gh.xy2loc(h_x, h_y + dy)[1]),
            (gh.xy2loc(h_x + 2 * size, h_y)[0], zone.lat),
            (gh.xy2loc(h_x + size, h_y)[0], gh.xy2loc(h_x, h_y - dy)[1]),
            (gh.xy2loc(h_x - size, h_y)[0], gh.xy2loc(h_x, h_y - dy)[1]),
        ]
        actual = gh.cell_polygon_lonlat(zone.x, zone.y, level)[:-1]
        for (alon, alat), (elon, elat) in zip(actual, expected):
            self.assertAlmostEqual(alon, elon, places=9)
            self.assertAlmostEqual(alat, elat, places=9)


if __name__ == "__main__":
    unittest.main()
