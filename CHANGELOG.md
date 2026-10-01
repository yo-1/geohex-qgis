# Changelog

## [0.1.0] - 未リリース
- 初版: ポリゴンレイヤと重なる GeoHex v3 セルを生成する Processing アルゴリズム
- GeoHex v3.2 公式コアの純Python移植と単体テスト
- 出力属性 `area_m2`（WGS84 楕円体上の面積）
- ラインレイヤ（路網など）を入力可能に。ライン入力は常に「重なるセル」で判定
- 実行ログに入力フィーチャ数・パート数を出力し、出力0件のときは警告を表示
- 他実装（PL/pgSQL・ES2015・Python）の例を単体テストに追加。コード前方一致の性質（9セルの塊）のテストを追加
- `docs/GEOHEX_GUIDE.md`（性質・利点と欠点・ユースケース・置き換え評価）を追加
- README・LICENSE・about に GeoHex の帰属表示（MIT, © 2009 @sa2da）を明記
- README にレベル番号が版・実装で異なる旨の注意を追記
- `tools/compare_grids.py`（GeoHex と H3 のセルの面積・辺長・中心からの距離を主要地点で比較する開発用ツール）を追加。`docs/GEOHEX_GUIDE.md` に平面直角座標系と主要地点のセルの大きさの章を追加
