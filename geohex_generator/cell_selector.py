"""ポリゴン（陸域など）と重なる GeoHex セルを列挙する（QGIS非依存）。

設計方針:
- QGIS の API に依存する部分は LandOracle（プロトコル）の実装側に閉じ込める。
  これにより、この列挙ロジックは QGIS なしで単体テストできる。
- 入力ポリゴンの bbox を「ブロック」に分割し、ブロック単位で
    1. 陸域と無関係 → スキップ
    2. 全セルの中心が陸域内 → 全セルを無条件採用
    3. それ以外 → セルごとに判定
  とすることで、日本のような細長い・島の多い形状でも高速に処理する。

座標の扱い:
- セル中心は、メルカトル平面上で X = 3h*u, Y = sqrt(3)*h*v (u+v が偶数) の格子。
  GeoHex の (x, y) とは x = (u+v)/2, y = (v-u)/2 で相互変換できる。
- メルカトル平面上の軸平行な長方形は、経緯度上でも軸平行な長方形になる
  （経線・緯線が直線のまま）ので、ブロックの判定は経緯度の長方形で厳密に行える。
"""

from __future__ import annotations

import math
from typing import Callable, Iterator, List, Optional, Protocol, Set, Tuple

from .geohex_v3 import (
    cell_center_mercator,
    cell_polygon_lonlat,
    hex_size,
    loc2xy,
    xy2loc,
)

MODE_INTERSECTS = "intersects"  # セルが陸域と1点でも重なれば採用（被覆）
MODE_CENTER = "center"  # セル中心が陸域内にあれば採用

DEFAULT_TILE_SIZE = 32  # 1ブロックあたりの格子数（一辺）
_MAX_MERCATOR_LAT = 85.0511287798  # Webメルカトルの北緯・南緯の限界

BBox = Tuple[float, float, float, float]  # (lon_min, lat_min, lon_max, lat_max)
Ring = List[Tuple[float, float]]


class LandOracle(Protocol):
    """陸域ジオメトリに対する問い合わせ。すべて経緯度(WGS84)で答える。"""

    def rect_intersects(self, lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> bool:
        """長方形が陸域と重なるか（False と答えてよいのは確実に重ならないときのみ）。"""

    def rect_contained(self, lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> bool:
        """長方形が陸域に完全に含まれるか（True と答えてよいのは確実に含まれるときのみ）。"""

    def polygon_intersects(self, ring: Ring) -> bool:
        """閉じた外周リングのポリゴンが陸域と重なるか。"""

    def point_covered(self, lon: float, lat: float) -> bool:
        """点が陸域内（境界を含む）にあるか。"""


def _uv_range(bbox: BBox, level: int) -> Tuple[int, int, int, int]:
    """bbox と重なり得るセルの中心が取り得る (u, v) の範囲。"""
    lon_min, lat_min, lon_max, lat_max = bbox
    lat_min = max(lat_min, -_MAX_MERCATOR_LAT)
    lat_max = min(lat_max, _MAX_MERCATOR_LAT)
    x_min, y_min = loc2xy(lon_min, lat_min)
    x_max, y_max = loc2xy(lon_max, lat_max)

    h_size = hex_size(level)
    step_u = 3 * h_size
    step_v = math.sqrt(3) * h_size

    # セルは中心から水平方向に ±2h、垂直方向に ±sqrt(3)h まで広がる。
    u_limit = 3 ** (level + 2)  # 世界の端（±180度）
    u_min = max(math.ceil((x_min - 2 * h_size) / step_u), -u_limit)
    u_max = min(math.floor((x_max + 2 * h_size) / step_u), u_limit)
    v_min = math.ceil((y_min - step_v) / step_v)
    v_max = math.floor((y_max + step_v) / step_v)
    return u_min, u_max, v_min, v_max


def estimate_candidate_count(bbox: BBox, level: int) -> int:
    """bbox を覆う候補セル数の概算（実際の出力数ではない）。"""
    u_min, u_max, v_min, v_max = _uv_range(bbox, level)
    if u_max < u_min or v_max < v_min:
        return 0
    return ((u_max - u_min + 1) * (v_max - v_min + 1)) // 2


def _cell_selected(x: int, y: int, level: int, mode: str, oracle: LandOracle) -> bool:
    if mode == MODE_CENTER:
        center_x, center_y = cell_center_mercator(x, y, level)
        lon, lat = xy2loc(center_x, center_y)
        return oracle.point_covered(lon, lat)
    return oracle.polygon_intersects(cell_polygon_lonlat(x, y, level))


def select_cells(
    bbox: BBox,
    level: int,
    oracle: LandOracle,
    mode: str = MODE_INTERSECTS,
    seen: Optional[Set[Tuple[int, int]]] = None,
    tile_size: int = DEFAULT_TILE_SIZE,
    is_canceled: Optional[Callable[[], bool]] = None,
) -> Iterator[Tuple[int, int]]:
    """採用するセルの (x, y) を順に返す。

    seen に既出のセルは返さない。返したセルは seen に追加される
    （複数ポリゴンにまたがって同じセルを重複出力しないため）。
    """
    if mode not in (MODE_INTERSECTS, MODE_CENTER):
        raise ValueError(f"未知の mode です: {mode!r}")
    if tile_size < 1:
        raise ValueError("tile_size は 1 以上にしてください")
    if seen is None:
        seen = set()

    h_size = hex_size(level)
    step_u = 3 * h_size
    step_v = math.sqrt(3) * h_size
    u_min, u_max, v_min, v_max = _uv_range(bbox, level)

    for block_u in range(u_min, u_max + 1, tile_size):
        block_u_end = min(block_u + tile_size - 1, u_max)
        for block_v in range(v_min, v_max + 1, tile_size):
            if is_canceled is not None and is_canceled():
                return
            block_v_end = min(block_v + tile_size - 1, v_max)

            # ブロック内の全セル中心を含む長方形（メルカトル→経緯度）
            c_lon_min, c_lat_min = xy2loc(block_u * step_u, block_v * step_v)
            c_lon_max, c_lat_max = xy2loc(block_u_end * step_u, block_v_end * step_v)

            if mode == MODE_CENTER:
                may_hit = oracle.rect_intersects(c_lon_min, c_lat_min, c_lon_max, c_lat_max)
            else:
                # ブロック内の全セルを覆う長方形
                k_lon_min, k_lat_min = xy2loc(
                    block_u * step_u - 2 * h_size, block_v * step_v - step_v
                )
                k_lon_max, k_lat_max = xy2loc(
                    block_u_end * step_u + 2 * h_size, block_v_end * step_v + step_v
                )
                may_hit = oracle.rect_intersects(k_lon_min, k_lat_min, k_lon_max, k_lat_max)
            if not may_hit:
                continue

            # 全セルの中心が陸域内なら、どちらのモードでも全セル採用でよい。
            take_all = oracle.rect_contained(c_lon_min, c_lat_min, c_lon_max, c_lat_max)

            for u in range(block_u, block_u_end + 1):
                for v in range(block_v, block_v_end + 1):
                    if (u + v) & 1:  # 格子点は u+v が偶数のときだけ
                        continue
                    key = ((u + v) // 2, (v - u) // 2)
                    if key in seen:
                        continue
                    if not take_all and not _cell_selected(key[0], key[1], level, mode, oracle):
                        continue
                    seen.add(key)
                    yield key


__all__ = [
    "MODE_CENTER",
    "MODE_INTERSECTS",
    "DEFAULT_TILE_SIZE",
    "LandOracle",
    "estimate_candidate_count",
    "select_cells",
]
