"""GeoHex グリッド生成アルゴリズム（QGIS Processing）。

ポリゴンレイヤ（日本の陸域など）と重なる GeoHex v3 のセルを、ポリゴンとして出力する。
実際のセル列挙ロジックは cell_selector.py（QGIS非依存）にあり、
このファイルは QGIS の入出力・座標変換・ジオメトリ判定との橋渡しだけを担当する。

Processing アルゴリズムなので、ツールボックス／バッチ処理／モデラー／Pythonコンソール
（processing.run）のいずれからも同じ形で実行でき、実行はバックグラウンドスレッドで行われる。
processAlgorithm 内では QgsProject や GUI に触れないこと。
"""

import time

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingFeatureSource,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QCoreApplication, QMetaType

from .cell_selector import MODE_CENTER, MODE_INTERSECTS, select_cells
from .geohex_v3 import MAX_LEVEL, MIN_LEVEL, cell_polygon_lonlat, get_zone_by_xy, hex_size

WGS84_AUTHID = "EPSG:4326"
FLUSH_BATCH_SIZE = 5000


def _resolve(*getters):
    """QGISのバージョン差でenum名が異なる場合に備え、最初に解決できたものを返す。"""
    for getter in getters:
        try:
            return getter()
        except AttributeError:
            continue
    return None


# QGIS 3.36 以降はスコープ付きenum、それ以前は旧名。QGIS 4 でも旧名が残る保証がないため両対応。
_SOURCE_POLYGON = _resolve(
    lambda: Qgis.ProcessingSourceType.VectorPolygon,
    lambda: QgsProcessing.TypeVectorPolygon,
)
_NUMBER_INTEGER = _resolve(
    lambda: Qgis.ProcessingNumberParameterType.Integer,
    lambda: QgsProcessingParameterNumber.Integer,
)
_SKIP_VALIDITY_CHECK = _resolve(
    lambda: Qgis.ProcessingFeatureSourceFlag.SkipGeometryValidityChecks,
    lambda: QgsProcessingFeatureSource.FlagSkipGeometryValidityChecks,
)
_WKB_POLYGON = Qgis.WkbType.Polygon
_GEOMETRY_POLYGON = Qgis.GeometryType.Polygon


class PreparedLandOracle:
    """陸域（1つのポリゴンパート）に対する判定。GEOSの prepared geometry で高速化する。

    注意: QgsGeometryEngine は元ジオメトリへの生ポインタを保持する。
    self._land を保持し続けることでポインタの寿命を保証している。
    """

    _EPS = 1e-9  # 幅・高さが0の長方形は不正ポリゴンになるため、わずかに広げる（判定は安全側に倒れる）

    def __init__(self, land: QgsGeometry):
        self._land = land
        self._engine = QgsGeometry.createGeometryEngine(land.constGet())
        self._engine.prepareGeometry()

    def _rect(self, lon_min, lat_min, lon_max, lat_max) -> QgsGeometry:
        if lon_max - lon_min < 2 * self._EPS:
            lon_min -= self._EPS
            lon_max += self._EPS
        if lat_max - lat_min < 2 * self._EPS:
            lat_min -= self._EPS
            lat_max += self._EPS
        return QgsGeometry.fromRect(QgsRectangle(lon_min, lat_min, lon_max, lat_max))

    def rect_intersects(self, lon_min, lat_min, lon_max, lat_max) -> bool:
        rect = self._rect(lon_min, lat_min, lon_max, lat_max)
        return self._engine.intersects(rect.constGet())

    def rect_contained(self, lon_min, lat_min, lon_max, lat_max) -> bool:
        rect = self._rect(lon_min, lat_min, lon_max, lat_max)
        return self._engine.contains(rect.constGet())

    def polygon_intersects(self, ring) -> bool:
        polygon = QgsGeometry.fromPolygonXY([[QgsPointXY(lon, lat) for lon, lat in ring]])
        return self._engine.intersects(polygon.constGet())

    def point_covered(self, lon, lat) -> bool:
        return self._engine.intersects(QgsPoint(lon, lat))


class GenerateGeoHexGridAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    LEVEL = "LEVEL"
    MODE = "MODE"
    MAX_CELLS = "MAX_CELLS"
    OUTPUT = "OUTPUT"

    _MODES = (MODE_INTERSECTS, MODE_CENTER)

    # ---- メタ情報 -------------------------------------------------------

    def tr(self, text):
        return QCoreApplication.translate("GenerateGeoHexGridAlgorithm", text)

    def createInstance(self):
        return GenerateGeoHexGridAlgorithm()

    def name(self):
        return "generate_geohex_grid"

    def displayName(self):
        return self.tr("GeoHexグリッドを作成（ポリゴン範囲）")

    def group(self):
        return self.tr("GeoHex")

    def groupId(self):
        return "geohex"

    def tags(self):
        return ["geohex", "hex", "hexagon", "grid", "六角形", "メッシュ", "グリッド"]

    def shortHelpString(self):
        return self.tr(
            "入力ポリゴン（例: 日本の陸域）と重なる GeoHex v3 のセルを、ポリゴンとして出力します。\n\n"
            "・出力CRSは EPSG:4326。頂点は公式実装(getHexCoords)と同じ6点です。\n"
            "・属性 area_m2 は、6頂点のポリゴンを WGS84 楕円体上で測った面積（m²）です。"
            "フィールド計算機の $area（楕円体 WGS84 設定時）と同じ方法です。\n"
            "・GeoHexはメルカトル平面上の正六角形です。地上の面積は緯度により異なります"
            "（面積は概ね cos²(緯度) 倍）。面積の均等性が必要な用途には向きません。\n"
            "・レベルを1つ上げるとセル数は約9倍になります。日本全域ならレベル6〜7が目安です。\n"
            "・『中心が陸域内』を選ぶと、セルより小さい島や細長い陸域は抜け落ちます。"
            "取りこぼしたくない場合は『重なるセル』を使ってください。\n"
            "・セルは丸ごと出力します（陸域でクリップしません）。クリップや面積集計が必要な場合は、"
            "出力に対して標準の『交差』ツールを使ってください。\n"
            "・無効なジオメトリは自動修復を試みます。入力は WGS84/JGD2011 など任意のCRSで構いません。"
        )

    # ---- パラメータ -----------------------------------------------------

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr("対象範囲ポリゴンレイヤ（陸域など）"),
                [_SOURCE_POLYGON],
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LEVEL,
                self.tr("GeoHexレベル（0〜15）"),
                type=_NUMBER_INTEGER,
                defaultValue=6,
                minValue=MIN_LEVEL,
                maxValue=MAX_LEVEL,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                self.tr("セルの抽出条件"),
                options=[
                    self.tr("陸域と少しでも重なるセル（被覆・推奨）"),
                    self.tr("セル中心が陸域内にあるセル"),
                ],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_CELLS,
                self.tr("出力セル数の上限（暴走防止）"),
                type=_NUMBER_INTEGER,
                defaultValue=2_000_000,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("GeoHexセル"),
                type=_SOURCE_POLYGON,
            )
        )

    # ---- 実行 -----------------------------------------------------------

    @staticmethod
    def _output_fields() -> QgsFields:
        fields = QgsFields()
        fields.append(QgsField("code", QMetaType.Type.QString))
        fields.append(QgsField("level", QMetaType.Type.Int))
        fields.append(QgsField("x", QMetaType.Type.LongLong))
        fields.append(QgsField("y", QMetaType.Type.LongLong))
        fields.append(QgsField("lon", QMetaType.Type.Double))
        fields.append(QgsField("lat", QMetaType.Type.Double))
        fields.append(QgsField("area_m2", QMetaType.Type.Double))
        return fields

    def processAlgorithm(self, parameters, context, feedback):
        started = time.monotonic()

        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        if not source.sourceCrs().isValid():
            raise QgsProcessingException(self.tr("入力レイヤのCRSが未設定です。CRSを設定してから実行してください。"))

        level = self.parameterAsInt(parameters, self.LEVEL, context)
        mode = self._MODES[self.parameterAsEnum(parameters, self.MODE, context)]
        max_cells = self.parameterAsInt(parameters, self.MAX_CELLS, context)

        wgs84 = QgsCoordinateReferenceSystem(WGS84_AUTHID)
        fields = self._output_fields()
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, _WKB_POLYGON, wgs84
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        transform = QgsCoordinateTransform(source.sourceCrs(), wgs84, context.transformContext())

        # 面積は出力CRS(EPSG:4326)の度単位ではなく、WGS84楕円体上の m² で求める。
        distance_area = QgsDistanceArea()
        distance_area.setSourceCrs(wgs84, context.transformContext())
        if not distance_area.setEllipsoid("WGS84"):
            raise QgsProcessingException(self.tr("楕円体 WGS84 を設定できませんでした。"))

        h_size = hex_size(level)
        feedback.pushInfo(
            self.tr(
                "レベル {level}: セルの外接円半径 約 {radius:.1f} m（メルカトル平面上。"
                "地上では緯度に応じて cos(緯度) 倍に縮む）"
            ).format(level=level, radius=2 * h_size)
        )

        total = source.featureCount()
        seen = set()
        batch = []
        written = 0
        part_count = 0
        repaired_count = 0
        skipped_count = 0

        def flush():
            nonlocal written
            if not batch:
                return
            if not sink.addFeatures(batch):
                raise QgsProcessingException(self.tr("出力レイヤへの書き込みに失敗しました。"))
            written += len(batch)
            batch.clear()

        if _SKIP_VALIDITY_CHECK is None:
            features = source.getFeatures()
        else:
            # 無効ジオメトリで Processing 全体を止めず、こちらで修復する。
            features = source.getFeatures(QgsFeatureRequest(), _SKIP_VALIDITY_CHECK)

        for index, feature in enumerate(features):
            if feedback.isCanceled():
                break
            if total > 0:
                feedback.setProgress(100.0 * index / total)

            geometry = feature.geometry()
            if geometry.isNull() or geometry.isEmpty():
                skipped_count += 1
                continue

            geometry = QgsGeometry(geometry)  # 元のフィーチャを書き換えないためコピー
            if geometry.transform(transform) != Qgis.GeometryOperationResult.Success:
                feedback.reportError(
                    self.tr("フィーチャ {id} の座標変換に失敗したためスキップしました。").format(id=feature.id())
                )
                skipped_count += 1
                continue

            if not geometry.isGeosValid():
                geometry = geometry.makeValid()
                repaired_count += 1

            for part in geometry.asGeometryCollection():
                if part.isNull() or part.isEmpty() or part.type() != _GEOMETRY_POLYGON:
                    continue
                part_count += 1

                bbox = part.boundingBox()
                oracle = PreparedLandOracle(part)  # part をこのスコープで保持（寿命保証）
                for x, y in select_cells(
                    (bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()),
                    level,
                    oracle,
                    mode,
                    seen=seen,
                    is_canceled=feedback.isCanceled,
                ):
                    if len(seen) > max_cells:
                        raise QgsProcessingException(
                            self.tr(
                                "出力セル数が上限（{limit}）を超えました。レベルを下げるか、"
                                "対象範囲を絞るか、上限値を引き上げてください。"
                            ).format(limit=max_cells)
                        )
                    zone = get_zone_by_xy(x, y, level)
                    ring = cell_polygon_lonlat(x, y, level)
                    cell_geometry = QgsGeometry.fromPolygonXY(
                        [[QgsPointXY(lon, lat) for lon, lat in ring]]
                    )
                    out = QgsFeature(fields)
                    out.setGeometry(cell_geometry)
                    out.setAttributes(
                        [
                            zone.code,
                            level,
                            x,
                            y,
                            zone.lon,
                            zone.lat,
                            distance_area.measureArea(cell_geometry),
                        ]
                    )
                    batch.append(out)
                    if len(batch) >= FLUSH_BATCH_SIZE:
                        flush()

        flush()

        if repaired_count:
            feedback.pushWarning(
                self.tr("{n} 件のフィーチャで無効なジオメトリを自動修復しました。").format(n=repaired_count)
            )
        if skipped_count:
            feedback.pushWarning(
                self.tr("{n} 件のフィーチャ（空ジオメトリまたは座標変換失敗）をスキップしました。").format(n=skipped_count)
            )
        if feedback.isCanceled():
            feedback.pushWarning(self.tr("キャンセルされたため、出力は途中までです。"))

        feedback.pushInfo(
            self.tr("完了: ポリゴンパート {parts} 件 → {cells} セル出力（{sec:.1f} 秒）").format(
                parts=part_count, cells=written, sec=time.monotonic() - started
            )
        )
        return {self.OUTPUT: dest_id}

