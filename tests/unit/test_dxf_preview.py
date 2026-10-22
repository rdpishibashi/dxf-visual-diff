"""
このテストが守るもの: 差分DXFのブラウザ内プレビュー（utils/dxf_preview.py）が、
5カテゴリ別レイヤーを共通の座標系で重ね、自己完結HTMLとして返すこと。
合成DXF（ezdxf.new()）だけで検証する（実データ不要）。
"""

import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ezdxf
import pytest

from utils import dxf_preview
from utils.dxf_preview import PREVIEW_LAYERS, build_viewer_html, render_layers_svg

ALL_LAYERS = [name for name, _, _ in PREVIEW_LAYERS]


def _make_dxf(layers_with_entities, off_layers=(), mtext=None):
    """指定レイヤーに線を1本ずつ置いた差分DXF風のバイト列を作る。"""
    doc = ezdxf.new("R2010")
    for i, name in enumerate(layers_with_entities):
        doc.layers.add(name)
        doc.modelspace().add_line((i * 10, 0), (i * 10 + 5, 5), dxfattribs={"layer": name, "color": 4})
    for name in off_layers:
        doc.layers.get(name).off()  # 差分DXFの5カテゴリ層は既定でOFF
    if mtext:
        doc.layers.add("UNCHANGED") if "UNCHANGED" not in doc.layers else None
        doc.modelspace().add_mtext(mtext, dxfattribs={"layer": "UNCHANGED", "insert": (0, 20), "char_height": 2.5})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def test_all_five_layers_rendered_with_common_view_box_even_when_layer_off():
    """DXF上でOFFのレイヤーも描画され、全レイヤーが同一のviewBox（共通render_box）で重なる。"""
    data = _make_dxf(ALL_LAYERS, off_layers=ALL_LAYERS)
    result = render_layers_svg(data)
    assert not result.empty
    assert [layer.name for layer in result.layers] == ALL_LAYERS
    assert all(layer.count == 1 and layer.inner_svg for layer in result.layers)
    html_text = build_viewer_html(result)
    # 外側SVGのviewBoxは1つだけ（レイヤーごとの<svg>が混ざらない）
    assert html_text.count("<svg") == 1
    assert result.view_box[0] > 0 and result.view_box[1] > 0


def test_css_class_names_do_not_collide_between_layers():
    """各レイヤーのクラス名にレイヤー固有の接頭辞が付き、レイヤー間で衝突しない。"""
    result = render_layers_svg(_make_dxf(ALL_LAYERS))
    prefixes = []
    for index, layer in enumerate(result.layers):
        classes = set(re.findall(r'class="(\w+)"', layer.inner_svg)) - {"layer"}
        assert classes, layer.name
        assert all(c.startswith(f"L{index}C") for c in classes), (layer.name, classes)
        prefixes.append(f"L{index}C")
    assert len(set(prefixes)) == len(ALL_LAYERS)


def test_missing_offset_layers_are_disabled_not_error():
    """オフセット検出なし（OFFSET層がDXFに無い）でも例外にならず、該当2層は件数0で無効表示になる。"""
    result = render_layers_svg(_make_dxf(["NEW_ADDED", "OLD_DELETED", "UNCHANGED"]))
    counts = {layer.name: layer.count for layer in result.layers}
    assert counts["NEW_UNCHANGED_OFFSET"] == 0 and counts["OLD_UNCHANGED_OFFSET"] == 0
    html_text = build_viewer_html(result)
    assert len(re.findall(r'<input type="checkbox" data-layer="[A-Z_]+" disabled>', html_text)) == 2


def test_empty_layer_among_filled_layers_does_not_break():
    """削除0件のような空レイヤーが混ざっても、他レイヤーは描画される。"""
    result = render_layers_svg(_make_dxf(["NEW_ADDED", "UNCHANGED"]))
    deleted = next(layer for layer in result.layers if layer.name == "OLD_DELETED")
    assert deleted.count == 0 and deleted.inner_svg == ""
    assert not result.empty


def test_identical_files_only_unchanged_layer():
    """同一ファイル比較（差分0、UNCHANGEDのみ）でも描画できる。"""
    result = render_layers_svg(_make_dxf(["UNCHANGED"]))
    assert not result.empty
    assert [layer.count for layer in result.layers if layer.name == "UNCHANGED"] == [1]


def test_zero_entities_returns_empty_result_and_message():
    """エンティティ0件のDXFは例外にせず empty=True で「描画対象なし」を返す。"""
    result = render_layers_svg(_make_dxf([]))
    assert result.empty
    assert "描画対象のエンティティがありません" in build_viewer_html(result)


def test_entities_on_non_preview_layers_only_is_empty():
    """プレビュー対象外レイヤー（OLD_ALL等の複製層）にしか図形が無い場合も empty 扱い。"""
    result = render_layers_svg(_make_dxf(["OLD_ALL", "NEW_ALL"]))
    assert result.empty


def test_html_has_no_external_references():
    """HTMLは自己完結で、外部URL（SVG名前空間を除く）を含まない。CDNに依存しない設計の保証。"""
    html_text = build_viewer_html(render_layers_svg(_make_dxf(ALL_LAYERS)))
    urls = [u for u in re.findall(r"https?://[^\"' )<]+", html_text) if u != "http://www.w3.org/2000/svg"]
    assert urls == []
    assert "<script src" not in html_text and "<link " not in html_text


def test_input_bytes_not_modified_by_font_replacement():
    """日本語フォント差し替えは読み込んだdocのコピーに対してのみ行い、元のDXFバイト列は変えない。"""
    data = _make_dxf(ALL_LAYERS)
    before = bytes(data)
    render_layers_svg(data)
    assert data == before


def test_without_cjk_font_returns_none_and_still_renders(monkeypatch):
    """日本語フォントが見つからない環境では cjk_font=None で描画は継続し、HTMLに注意書きが出る。"""
    monkeypatch.setattr(dxf_preview, "find_cjk_font", lambda: None)
    result = render_layers_svg(_make_dxf(ALL_LAYERS, mtext="日本語テキスト"))
    assert result.cjk_font is None and not result.empty
    assert "日本語フォントが見つからない" in build_viewer_html(result)


def test_japanese_mtext_is_drawn_when_cjk_font_available():
    """日本語フォントがある環境では日本語MTEXTが描画される（UNCHANGEDレイヤーの図形が線1本より増える）。"""
    if dxf_preview.find_cjk_font() is None:
        pytest.skip("この環境には日本語フォントが無い（Cloudでは packages.txt で導入）")
    with_text = render_layers_svg(_make_dxf(["UNCHANGED"], mtext="日本語テキスト"))
    without = render_layers_svg(_make_dxf(["UNCHANGED"]))
    unchanged = lambda r: next(l for l in r.layers if l.name == "UNCHANGED")
    assert len(unchanged(with_text).inner_svg) > len(unchanged(without).inner_svg)
