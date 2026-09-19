"""Processing プロバイダ。アルゴリズムをツールボックスに登録する。"""

from qgis.core import QgsProcessingProvider

from .algorithm import GenerateGeoHexGridAlgorithm


class GeoHexProvider(QgsProcessingProvider):
    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(GenerateGeoHexGridAlgorithm())

    def id(self):
        return "geohex"

    def name(self):
        return "GeoHex"

    def longName(self):  # noqa: N802
        return "GeoHex Generator"
