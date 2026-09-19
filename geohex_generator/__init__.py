"""GeoHex Generator - QGIS プラグインのエントリポイント。"""


def classFactory(iface):  # noqa: N802 (QGIS が要求する名前)
    """QGIS がプラグインをロードするときに呼ぶ。QGIS 依存の import は遅延させる。"""
    from .plugin import GeoHexGeneratorPlugin

    return GeoHexGeneratorPlugin(iface)
