"""cell_selector（ブロック分割による採用セル列挙）の単体テスト。

QGIS なしで動かすため、「陸域」を経緯度上の長方形の和集合で表すテスト用オラクルを使う。
期待値は、ブロック分割や全セル採用の最適化を使わず全セルを総当たりで判定した結果。
"""

import math
import unittest

from geohex_generator import geohex_v3 as gh
from geohex_generator.cell_selector import (
    MODE_CENTER,
    MODE_INTERSECTS,
    estimate_candidate_count,
    select_cells,
)


def _rects_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _convex_polygon_hits_rect(points, rect):
    """凸多角形と軸平行長方形の交差判定（分離軸判定）。"""
    corners = [(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])]
    axes = [(1.0, 0.0), (0.0, 1.0)]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        axes.append((-(y2 - y1), x2 - x1))
    for ax, ay in axes:
        p = [px * ax + py * ay for px, py in points]
        c = [cx * ax + cy * ay for cx, cy in corners]
        if max(p) < min(c) or max(c) < min(p):
            return False
    return True


class RectUnionOracle:
    """陸域＝経緯度長方形の和集合。rect_contained は保守的（単一長方形に含まれる場合のみ True）。"""

    def __init__(self, rects):
        self.rects = rects

    def rect_intersects(self, lon_min, lat_min, lon_max, lat_max):
        return any(_rects_overlap((lon_min, lat_min, lon_max, lat_max), r) for r in self.rects)

    def rect_contained(self, lon_min, lat_min, lon_max, lat_max):
        return any(
            r[0] <= lon_min and lon_max <= r[2] and r[1] <= lat_min and lat_max <= r[3]
            for r in self.rects
        )

    def polygon_intersects(self, ring):
        return any(_convex_polygon_hits_rect(ring, r) for r in self.rects)

    def point_covered(self, lon, lat):
        return any(r[0] <= lon <= r[2] and r[1] <= lat <= r[3] for r in self.rects)


def brute_force_oracle(oracle, bbox, level, mode):
    """全格子点を総当たりで判定した期待値（ブロック分割・全セル採用の最適化を使わない）。"""
    lon_min, lat_min, lon_max, lat_max = bbox
    h = gh.hex_size(level)
    step_u, step_v = 3 * h, math.sqrt(3) * h
    x0, y0 = gh.loc2xy(lon_min, lat_min)
    x1, y1 = gh.loc2xy(lon_max, lat_max)
    expected = set()
    for u in range(math.floor(x0 / step_u) - 4, math.ceil(x1 / step_u) + 5):
        for v in range(math.floor(y0 / step_v) - 4, math.ceil(y1 / step_v) + 5):
            if (u + v) & 1:
                continue
            x, y = (u + v) // 2, (v - u) // 2
            if mode == MODE_CENTER:
                cx, cy = gh.cell_center_mercator(x, y, level)
                lon, lat = gh.xy2loc(cx, cy)
                hit = oracle.point_covered(lon, lat)
            else:
                hit = oracle.polygon_intersects(gh.cell_polygon_lonlat(x, y, level))
            if hit:
                expected.add((x, y))
    return expected


def brute_force(rects, level, mode):
    bbox = (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )
    return brute_force_oracle(RectUnionOracle(rects), bbox, level, mode)


def _axes(points):
    """凸多角形（または線分）の分離軸候補。線分は法線と方向の両方を返す。"""
    n = len(points)
    axes = []
    for i in range(n if n > 2 else 1):
        (x1, y1), (x2, y2) = points[i], points[(i + 1) % n]
        axes.append((-(y2 - y1), x2 - x1))
        if n == 2:
            axes.append((x2 - x1, y2 - y1))
    return axes


def convex_shapes_hit(a, b):
    """凸多角形／線分どうしの交差判定（分離軸判定）。"""
    for ax, ay in _axes(a) + _axes(b):
        pa = [x * ax + y * ay for x, y in a]
        pb = [x * ax + y * ay for x, y in b]
        if max(pa) < min(pb) or max(pb) < min(pa):
            return False
    return True


class SegmentOracle:
    """路網のような「線」を模したオラクル。線は面積を持たないので rect_contained は常に False。"""

    def __init__(self, start, end):
        self.segment = [start, end]

    def rect_intersects(self, lon_min, lat_min, lon_max, lat_max):
        corners = [(lon_min, lat_min), (lon_max, lat_min), (lon_max, lat_max), (lon_min, lat_max)]
        return convex_shapes_hit(self.segment, corners)

    def rect_contained(self, lon_min, lat_min, lon_max, lat_max):
        return False

    def polygon_intersects(self, ring):
        return convex_shapes_hit(self.segment, ring[:-1])

    def point_covered(self, lon, lat):
        return False


L_SHAPE = [(139.0, 35.0, 139.8, 35.4), (139.4, 35.0, 139.8, 36.0)]
TINY_ISLET = [(139.700, 35.700, 139.7005, 35.7004)]  # セルよりはるかに小さい島
SLIVER = [(139.0, 35.0, 139.9, 35.0005)]  # 細長い帯


class SelectCellsTest(unittest.TestCase):
    def _bbox(self, rects):
        return (
            min(r[0] for r in rects),
            min(r[1] for r in rects),
            max(r[2] for r in rects),
            max(r[3] for r in rects),
        )

    def test_matches_brute_force(self):
        for rects in (L_SHAPE, TINY_ISLET, SLIVER):
            for level in (5, 6, 7):
                for mode in (MODE_INTERSECTS, MODE_CENTER):
                    for tile_size in (1, 5, 32):
                        with self.subTest(rects=rects[0], level=level, mode=mode, tile=tile_size):
                            expected = brute_force(rects, level, mode)
                            actual = list(
                                select_cells(
                                    self._bbox(rects),
                                    level,
                                    RectUnionOracle(rects),
                                    mode,
                                    tile_size=tile_size,
                                )
                            )
                            self.assertEqual(len(actual), len(set(actual)), "重複出力")
                            self.assertEqual(set(actual), expected)

    def test_line_input_matches_brute_force(self):
        """線（路網）入力: 斜めの線分が通るセルだけが、ブロック分割でも過不足なく選ばれる。"""
        start, end = (139.00, 35.20), (139.60, 35.50)
        bbox = (start[0], start[1], end[0], end[1])
        for level in (6, 7):
            expected = brute_force_oracle(SegmentOracle(start, end), bbox, level, MODE_INTERSECTS)
            self.assertGreater(len(expected), 0)
            for tile_size in (1, 5, 32):
                with self.subTest(level=level, tile=tile_size):
                    actual = set(
                        select_cells(
                            bbox, level, SegmentOracle(start, end), MODE_INTERSECTS, tile_size=tile_size
                        )
                    )
                    self.assertEqual(actual, expected)

    def test_tiny_islet_is_covered_only_in_intersects_mode(self):
        oracle = RectUnionOracle(TINY_ISLET)
        bbox = self._bbox(TINY_ISLET)
        self.assertGreaterEqual(len(list(select_cells(bbox, 6, oracle, MODE_INTERSECTS))), 1)

    def test_seen_prevents_duplicates_across_calls(self):
        oracle = RectUnionOracle(L_SHAPE)
        bbox = self._bbox(L_SHAPE)
        seen = set()
        first = list(select_cells(bbox, 6, oracle, MODE_INTERSECTS, seen=seen))
        second = list(select_cells(bbox, 6, oracle, MODE_INTERSECTS, seen=seen))
        self.assertTrue(first)
        self.assertEqual(second, [])
        self.assertEqual(set(first), seen)

    def test_cancel_stops_iteration(self):
        oracle = RectUnionOracle(L_SHAPE)
        result = list(
            select_cells(self._bbox(L_SHAPE), 6, oracle, MODE_INTERSECTS, is_canceled=lambda: True)
        )
        self.assertEqual(result, [])

    def test_invalid_arguments(self):
        oracle = RectUnionOracle(L_SHAPE)
        with self.assertRaises(ValueError):
            list(select_cells(self._bbox(L_SHAPE), 6, oracle, mode="unknown"))
        with self.assertRaises(ValueError):
            list(select_cells(self._bbox(L_SHAPE), 6, oracle, tile_size=0))

    def test_estimate_candidate_count_is_upper_bound_scale(self):
        bbox = self._bbox(L_SHAPE)
        selected = len(brute_force(L_SHAPE, 6, MODE_INTERSECTS))
        self.assertGreaterEqual(estimate_candidate_count(bbox, 6), selected)


if __name__ == "__main__":
    unittest.main()
