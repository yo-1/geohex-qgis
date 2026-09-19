"""プラグイン本体。Processing プロバイダの登録と解除だけを行う。

ツールバーやメニューを追加しない設計にしているため、ロード／アンロードを繰り返しても
UI項目の重複登録は起きない。アンロード時は必ずプロバイダを解除すること。
"""

from qgis.core import QgsApplication

from .provider import GeoHexProvider


class GeoHexGeneratorPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initProcessing(self):  # noqa: N802
        self.provider = GeoHexProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
