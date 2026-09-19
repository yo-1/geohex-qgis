"""GeoHex v3.2 の純Python実装（QGIS非依存）。

移植元: hex_v3.2_core.js  (http://geohex.net/src/script/hex_v3.2_core.js)
    Copyright (c) 2009 @sa2da (http://twitter.com/sa2da)
    http://www.geohex.org
    Released under the MIT license.

公式JS実装との差異（意図的なもの）:
- JavaScriptの Math.round（0.5は正の無限大方向へ丸める）を _js_round で再現している。
  Pythonの round() は銀行丸めなので使わないこと。
- getXYByCode の先頭0埋めは、公式JSでは for 文の条件式内で d9xlen を
  インクリメントしているため反復回数が不足し得る。ここでは固定桁数で埋める。
  （日本周辺のコードは先頭3桁が100以上となるため影響しない。）
- ハードコードされたグローバル変数や Zone キャッシュは持たない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

H_KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
H_BASE = 20037508.34  # Webメルカトル(EPSG:3857)の半周長[m]
H_K = math.tan(math.pi * (30 / 180))

MIN_LEVEL = 0
MAX_LEVEL = 15


@dataclass(frozen=True)
class Zone:
    """GeoHexの1セル。lat/lon は WGS84 でのセル中心（経緯度）。"""

    code: str
    level: int
    x: int
    y: int
    lat: float
    lon: float


# ---------------------------------------------------------------------------
# 基本関数
# ---------------------------------------------------------------------------


def _check_level(level: int) -> None:
    if not MIN_LEVEL <= level <= MAX_LEVEL:
        raise ValueError(f"level は {MIN_LEVEL}〜{MAX_LEVEL} の整数で指定してください: {level}")


def _js_round(value: float) -> int:
    """JavaScript の Math.round と同じ丸め（.5 は切り上げ）。"""
    return math.floor(value + 0.5)


def hex_size(level: int) -> float:
    """レベルに対応する h_size（メルカトル平面上の長さ[m]）。

    六角形の外接円半径（＝辺の長さ）は 2 * h_size。
    """
    _check_level(level)
    return H_BASE / math.pow(3, level + 3)


def loc2xy(lon: float, lat: float) -> Tuple[float, float]:
    """WGS84経緯度 → メルカトル平面座標[m]。"""
    x = lon * H_BASE / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) / (math.pi / 180)
    y *= H_BASE / 180
    return x, y


def xy2loc(x: float, y: float) -> Tuple[float, float]:
    """メルカトル平面座標[m] → WGS84経緯度。戻り値は (lon, lat)。"""
    lon = (x / H_BASE) * 180
    lat = (y / H_BASE) * 180
    lat = 180 / math.pi * (2 * math.atan(math.exp(lat * math.pi / 180)) - math.pi / 2)
    return lon, lat


# ---------------------------------------------------------------------------
# セル位置(x, y)の計算
# ---------------------------------------------------------------------------


def adjust_xy(x: int, y: int, level: int) -> Tuple[int, int, int]:
    """世界の端（日付変更線付近）に出たセル位置を折り返す。"""
    rev = 0
    max_hsteps = 3 ** (level + 2)
    hsteps = abs(x - y)
    if hsteps == max_hsteps and x > y:
        x, y = y, x
        rev = 1
    elif hsteps > max_hsteps:
        dif = hsteps - max_hsteps
        dif_x = dif // 2
        dif_y = dif - dif_x
        if x > y:
            edge_x = x - dif_x
            edge_y = y + dif_y
            edge_x, edge_y = edge_y, edge_x
            x = edge_x + dif_x
            y = edge_y - dif_y
        elif y > x:
            edge_x = x + dif_x
            edge_y = y - dif_y
            edge_x, edge_y = edge_y, edge_x
            x = edge_x - dif_x
            y = edge_y + dif_y
    return x, y, rev


def get_xy_by_location(lat: float, lon: float, level: int) -> Tuple[int, int]:
    """経緯度が属するセルの位置 (x, y) を返す。"""
    _check_level(level)
    if not -90 < lat < 90:
        raise ValueError(f"緯度は -90 < lat < 90 の範囲で指定してください: {lat}")
    if not -180 <= lon <= 180:
        raise ValueError(f"経度は -180〜180 の範囲で指定してください: {lon}")

    h_size = hex_size(level)
    lon_grid, lat_grid = loc2xy(lon, lat)
    unit_x = 6 * h_size
    unit_y = 6 * h_size * H_K
    h_pos_x = (lon_grid + lat_grid / H_K) / unit_x
    h_pos_y = (lat_grid - H_K * lon_grid) / unit_y
    h_x_0 = math.floor(h_pos_x)
    h_y_0 = math.floor(h_pos_y)
    h_x_q = h_pos_x - h_x_0
    h_y_q = h_pos_y - h_y_0
    h_x = _js_round(h_pos_x)
    h_y = _js_round(h_pos_y)

    if h_y_q > -h_x_q + 1:
        if (h_y_q < 2 * h_x_q) and (h_y_q > 0.5 * h_x_q):
            h_x = h_x_0 + 1
            h_y = h_y_0 + 1
    elif h_y_q < -h_x_q + 1:
        if (h_y_q > (2 * h_x_q) - 1) and (h_y_q < (0.5 * h_x_q) + 0.5):
            h_x = h_x_0
            h_y = h_y_0

    x, y, _ = adjust_xy(h_x, h_y, level)
    return x, y


def _validate_code(code: str) -> int:
    """コードの形式を検証し、レベルを返す。"""
    if not isinstance(code, str) or len(code) < 2 + MIN_LEVEL:
        raise ValueError(f"GeoHexコードが短すぎます: {code!r}")
    level = len(code) - 2
    if level > MAX_LEVEL:
        raise ValueError(f"GeoHexコードが長すぎます(レベル{level}): {code!r}")
    if code[0] not in H_KEY or code[1] not in H_KEY:
        raise ValueError(f"先頭2文字は英字で指定してください: {code!r}")
    if not all(c in "012345678" for c in code[2:]):
        raise ValueError(f"3文字目以降は 0〜8 の数字で指定してください: {code!r}")
    return level


def get_xy_by_code(code: str) -> Tuple[int, int]:
    """GeoHexコードからセル位置 (x, y) を返す。"""
    level = _validate_code(code)

    h_dec9 = str(H_KEY.index(code[0]) * 30 + H_KEY.index(code[1])) + code[2:]
    c0, c1, c2 = h_dec9[0:1], h_dec9[1:2], h_dec9[2:3]
    if c0 in ("1", "5") and c1 != "" and c1 not in "125" and c2 != "" and c2 not in "125":
        h_dec9 = ("7" if c0 == "5" else "3") + h_dec9[1:]

    h_dec9 = h_dec9.rjust(level + 3, "0")

    h_dec3 = "".join(f"{int(ch) // 3}{int(ch) % 3}" for ch in h_dec9)
    dec_x = h_dec3[0::2]
    dec_y = h_dec3[1::2]

    h_x = 0
    h_y = 0
    for i in range(level + 3):
        h_pow = 3 ** (level + 2 - i)
        if dec_x[i] == "0":
            h_x -= h_pow
        elif dec_x[i] == "2":
            h_x += h_pow
        if dec_y[i] == "0":
            h_y -= h_pow
        elif dec_y[i] == "2":
            h_y += h_pow

    x, y, _ = adjust_xy(h_x, h_y, level)
    return x, y


# ---------------------------------------------------------------------------
# セル(x, y) → Zone / 形状
# ---------------------------------------------------------------------------


def cell_center_mercator(x: int, y: int, level: int) -> Tuple[float, float]:
    """セル中心のメルカトル平面座標 (X, Y)[m]。

    公式JSの getZoneByXY 内の h_lon / h_lat に相当する。
    整数の (u, v) = (x - y, x + y) を使うと X = 3h*u, Y = sqrt(3)*h*v と書ける。
    """
    h_size = hex_size(level)
    unit_x = 6 * h_size
    unit_y = 6 * h_size * H_K
    center_y = (H_K * x * unit_x + y * unit_y) / 2
    center_x = (center_y - y * unit_y) / H_K
    return center_x, center_y


def get_zone_by_xy(x: int, y: int, level: int) -> Zone:
    """セル位置 (x, y) から Zone（コード・中心経緯度）を作る。"""
    _check_level(level)
    h_x, h_y = x, y

    center_x, center_y = cell_center_mercator(x, y, level)
    z_loc_x, z_loc_y = xy2loc(center_x, center_y)

    max_hsteps = 3 ** (level + 2)
    if abs(h_x - h_y) == max_hsteps:
        if h_x > h_y:
            h_x, h_y = h_y, h_x
        z_loc_x = -180.0

    code3_x: List[int] = []
    code3_y: List[int] = []
    mod_x = h_x
    mod_y = h_y

    for i in range(level + 3):
        h_pow = 3 ** (level + 2 - i)
        half = (h_pow + 1) // 2  # Math.ceil(h_pow / 2) と同じ（h_pow は正の整数）

        if mod_x >= half:
            code3_x.append(2)
            mod_x -= h_pow
        elif mod_x <= -half:
            code3_x.append(0)
            mod_x += h_pow
        else:
            code3_x.append(1)

        if mod_y >= half:
            code3_y.append(2)
            mod_y -= h_pow
        elif mod_y <= -half:
            code3_y.append(0)
            mod_y += h_pow
        else:
            code3_y.append(1)

        # 東半球（経度 >= 0）および日付変更線上のセルに対する先頭3桁の補正。
        if i == 2 and (z_loc_x == -180 or z_loc_x >= 0):
            if (
                code3_x[0] == 2
                and code3_y[0] == 1
                and code3_x[1] == code3_y[1]
                and code3_x[2] == code3_y[2]
            ):
                code3_x[0] = 1
                code3_y[0] = 2
            elif (
                code3_x[0] == 1
                and code3_y[0] == 0
                and code3_x[1] == code3_y[1]
                and code3_x[2] == code3_y[2]
            ):
                code3_x[0] = 0
                code3_y[0] = 1

    digits = "".join(str(cx * 3 + cy) for cx, cy in zip(code3_x, code3_y))
    h_1 = int(digits[:3])
    code = H_KEY[h_1 // 30] + H_KEY[h_1 % 30] + digits[3:]

    return Zone(code=code, level=level, x=x, y=y, lat=z_loc_y, lon=z_loc_x)


def get_zone_by_location(lat: float, lon: float, level: int) -> Zone:
    """経緯度が属するセルを返す。"""
    x, y = get_xy_by_location(lat, lon, level)
    return get_zone_by_xy(x, y, level)


def get_zone_by_code(code: str) -> Zone:
    """GeoHexコードからセルを返す。"""
    level = _validate_code(code)
    x, y = get_xy_by_code(code)
    return get_zone_by_xy(x, y, level)


def cell_polygon_lonlat(x: int, y: int, level: int) -> List[Tuple[float, float]]:
    """セルの外周リング [(lon, lat), ...]（始点=終点で閉じた7点）。

    頂点は公式JSの getHexCoords と同じ順序（左→左上→右上→右→右下→左下）。
    六角形はメルカトル平面上の正六角形（上下の辺が水平）で、頂点のみを経緯度へ
    逆変換している。辺は経緯度上の直線で結ぶ（公式と同じ扱い）。
    """
    center_x, center_y = cell_center_mercator(x, y, level)
    h_size = hex_size(level)
    dy = math.tan(math.pi * (60 / 180)) * h_size

    lat_center = xy2loc(center_x, center_y)[1]
    lat_top = xy2loc(center_x, center_y + dy)[1]
    lat_bottom = xy2loc(center_x, center_y - dy)[1]
    lon_left = xy2loc(center_x - 2 * h_size, center_y)[0]
    lon_right = xy2loc(center_x + 2 * h_size, center_y)[0]
    lon_center_left = xy2loc(center_x - h_size, center_y)[0]
    lon_center_right = xy2loc(center_x + h_size, center_y)[0]

    ring = [
        (lon_left, lat_center),
        (lon_center_left, lat_top),
        (lon_center_right, lat_top),
        (lon_right, lat_center),
        (lon_center_right, lat_bottom),
        (lon_center_left, lat_bottom),
    ]
    ring.append(ring[0])
    return ring
