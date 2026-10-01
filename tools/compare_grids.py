"""GeoHex と H3 のセルの大きさ・形を、主要地点で比較する（開発用ツール）。

実行例（リポジトリのルートで）:
    python tools/compare_grids.py --geohex-only          # GeoHex のみ（追加パッケージ不要）
    pip install h3                                       # H3 も比較する場合
    python tools/compare_grids.py --geohex-levels 6 7 --h3-resolutions 7 8

面積・距離は、GeoHex と H3 のどちらも同じ関数で、WGS84 楕円体上で求める。
- 面積: 楕円体→等積補助球→ランベルト正積方位図法で、頂点を直線で結んだ多角形の面積
- 距離: Vincenty の逆解法（測地線距離）
- 辺の中点は、両端点の緯度・経度の平均（セルが小さいので誤差は無視できる）

H3 の部分は、h3 パッケージ v4 系の関数名を使う（v3 系の名前も試す）。
このツールの H3 側は、作者の環境では未実行。h3 のバージョン差で失敗したら Issue で知らせてほしい。
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geohex_generator import geohex_v3 as gh  # noqa: E402

A = 6378137.0
F = 1 / 298.257223563
B = A * (1 - F)
E2 = F * (2 - F)

# 概略座標（都市は代表点、端は岬・島の概略位置）。厳密な位置ではない。
LOCATIONS = [
    ("最南端 沖ノ鳥島", 20.4167, 136.0667),
    ("那覇", 26.2124, 127.6809),
    ("福岡", 33.5904, 130.4017),
    ("大阪", 34.6937, 135.5023),
    ("名古屋", 35.1815, 136.9066),
    ("東京", 35.6812, 139.7671),
    ("仙台", 38.2682, 140.8694),
    ("札幌", 43.0621, 141.3544),
    ("最北端 宗谷岬", 45.5227, 141.9366),
]


def geodesic_distance(lat1, lon1, lat2, lon2) -> float:
    """Vincenty の逆解法。戻り値は m。"""
    if lat1 == lat2 and lon1 == lon2:
        return 0.0
    big_l = math.radians(lon2 - lon1)
    u1 = math.atan((1 - F) * math.tan(math.radians(lat1)))
    u2 = math.atan((1 - F) * math.tan(math.radians(lat2)))
    sin_u1, cos_u1, sin_u2, cos_u2 = math.sin(u1), math.cos(u1), math.sin(u2), math.cos(u2)
    lam = big_l
    for _ in range(200):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.hypot(cos_u2 * sin_lam, cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lam)
        cos_sigma = sin_u1 * sin_u2 + cos_u1 * cos_u2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cos_u1 * cos_u2 * sin_lam / sin_sigma
        cos2_alpha = 1 - sin_alpha**2
        cos_2sm = cos_sigma - 2 * sin_u1 * sin_u2 / cos2_alpha if cos2_alpha else 0.0
        c = F / 16 * cos2_alpha * (4 + F * (4 - 3 * cos2_alpha))
        lam_prev = lam
        lam = big_l + (1 - c) * F * sin_alpha * (
            sigma + c * sin_sigma * (cos_2sm + c * cos_sigma * (-1 + 2 * cos_2sm**2))
        )
        if abs(lam - lam_prev) < 1e-12:
            break
    u_sq = cos2_alpha * (A * A - B * B) / (B * B)
    k1 = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    k2 = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    d_sigma = k2 * sin_sigma * (
        cos_2sm
        + k2 / 4 * (cos_sigma * (-1 + 2 * cos_2sm**2) - k2 / 6 * cos_2sm * (-3 + 4 * sin_sigma**2) * (-3 + 4 * cos_2sm**2))
    )
    return B * k1 * (sigma - d_sigma)


def _authalic(phi: float) -> float:
    e = math.sqrt(E2)
    def q(p):
        s = math.sin(p)
        return (1 - E2) * (s / (1 - E2 * s * s) - (1 / (2 * e)) * math.log((1 - e * s) / (1 + e * s)))
    return math.asin(q(phi) / q(math.pi / 2))


def polygon_area_m2(vertices, center) -> float:
    """vertices=[(lat, lon), ...]（閉じなくてよい）。ランベルト正積方位図法（補助球）上の面積。"""
    e = math.sqrt(E2)
    qp = (1 - E2) * (1 / (1 - E2) - (1 / (2 * e)) * math.log((1 - e) / (1 + e)))
    radius = A * math.sqrt(qp / 2)
    b0 = _authalic(math.radians(center[0]))
    l0 = math.radians(center[1])
    pts = []
    for lat, lon in vertices:
        b = _authalic(math.radians(lat))
        dl = math.radians(lon) - l0
        k = math.sqrt(2 / (1 + math.sin(b0) * math.sin(b) + math.cos(b0) * math.cos(b) * math.cos(dl)))
        x = radius * k * math.cos(b) * math.sin(dl)
        y = radius * k * (math.cos(b0) * math.sin(b) - math.sin(b0) * math.cos(b) * math.cos(dl))
        pts.append((x, y))
    total = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def cell_metrics(vertices, center) -> dict:
    """vertices=[(lat, lon)] は閉じていない頂点列。"""
    edges = [
        geodesic_distance(a[0], a[1], b[0], b[1]) for a, b in zip(vertices, vertices[1:] + vertices[:1])
    ]
    to_vertex = [geodesic_distance(center[0], center[1], v[0], v[1]) for v in vertices]
    mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(vertices, vertices[1:] + vertices[:1])]
    to_edge = [geodesic_distance(center[0], center[1], m[0], m[1]) for m in mids]
    return {
        "area_km2": polygon_area_m2(vertices, center) / 1e6,
        "edge_min": min(edges), "edge_max": max(edges),
        "circ_min": min(to_vertex), "circ_max": max(to_vertex),
        "in_min": min(to_edge), "in_max": max(to_edge),
    }


def geohex_cell(lat, lon, level):
    zone = gh.get_zone_by_location(lat, lon, level)
    ring = gh.cell_polygon_lonlat(zone.x, zone.y, level)[:-1]
    return zone.code, [(la, lo) for lo, la in ring], (zone.lat, zone.lon)


def h3_cell(lat, lon, res):
    import h3  # noqa: PLC0415  (任意依存)

    if hasattr(h3, "latlng_to_cell"):  # h3-py v4
        cell = h3.latlng_to_cell(lat, lon, res)
        boundary = list(h3.cell_to_boundary(cell))
        center = h3.cell_to_latlng(cell)
    else:  # h3-py v3
        cell = h3.geo_to_h3(lat, lon, res)
        boundary = list(h3.h3_to_geo_boundary(cell))
        center = h3.h3_to_geo(cell)
    return cell, [(p[0], p[1]) for p in boundary], (center[0], center[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--geohex-levels", type=int, nargs="*", default=[6, 7])
    parser.add_argument("--h3-resolutions", type=int, nargs="*", default=[7, 8])
    parser.add_argument("--geohex-only", action="store_true")
    args = parser.parse_args()

    systems = [("GeoHex", lv, geohex_cell) for lv in args.geohex_levels]
    if not args.geohex_only:
        try:
            import h3  # noqa: F401, PLC0415
        except ImportError:
            print("h3 が見つからないため GeoHex のみ出力します（pip install h3）", file=sys.stderr)
        else:
            systems += [("H3", r, h3_cell) for r in args.h3_resolutions]

    header = ("方式", "レベル", "地点", "面積km2", "辺長m(最小-最大)", "中心→頂点m(最小-最大)", "中心→辺中点m(最小-最大)", "ID")
    print("\t".join(header))
    for name, level, fn in systems:
        for label, lat, lon in LOCATIONS:
            cell_id, vertices, center = fn(lat, lon, level)
            m = cell_metrics(vertices, center)
            print("\t".join([
                name, str(level), label, f"{m['area_km2']:.4f}",
                f"{m['edge_min']:.0f}-{m['edge_max']:.0f}",
                f"{m['circ_min']:.0f}-{m['circ_max']:.0f}",
                f"{m['in_min']:.0f}-{m['in_max']:.0f}", str(cell_id),
            ]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
