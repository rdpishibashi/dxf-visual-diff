"""
このテストが守るもの: システムフォントが1つも無い環境（Streamlit Cloud）でも、差分DXFの
ブラウザ内プレビュー（utils/dxf_preview.py）が例外なく描画でき、日本語も同梱フォントで描かれること。

不具合: 2026-10-22、Streamlit Cloud でプレビューを開くと
「プレビューを表示できませんでした: no fonts available, not even fallback fonts」となった
（ezdxf の FontNotFoundError）。Cloud にはシステムフォントが無く、packages.txt の
fonts-ipaexfont-gothic も反映されなかった。ローカル macOS には ArialUni 等があるため再現しなかった。
修正: IPAex ゴシックを fonts/ に同梱し、描画前に ezdxf のフォント管理へ登録する。
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf
import pytest
from ezdxf.fonts import font_manager as ezdxf_font_manager
from ezdxf.fonts import fonts as ezdxf_fonts

from utils import dxf_preview


@pytest.fixture
def no_system_fonts(monkeypatch):
    """フォント管理を空にし、走査先にもフォントが無い（Cloud相当）状態にする。終了後に元へ戻す。"""
    manager = ezdxf_fonts.font_manager
    saved = manager.dumps()
    saved_fallback = manager._fallback_font_name
    saved_defaults = list(ezdxf_font_manager.DEFAULT_FONTS)
    manager.clear()
    manager._fallback_font_name = ""
    monkeypatch.setitem(ezdxf_font_manager.FONT_DIRECTORIES, manager.platform, [])
    yield manager
    manager.clear()
    manager.loads(saved)
    manager._fallback_font_name = saved_fallback
    ezdxf_font_manager.DEFAULT_FONTS[:] = saved_defaults


def _dxf_with_japanese_text() -> bytes:
    doc = ezdxf.new("R2010")
    doc.layers.add("UNCHANGED")
    msp = doc.modelspace()
    msp.add_line((0, 0), (50, 0), dxfattribs={"layer": "UNCHANGED"})
    msp.add_mtext("承認 AWG24(シールド線)", dxfattribs={"layer": "UNCHANGED", "insert": (0, 10), "char_height": 2.5})
    msp.add_text("検図", dxfattribs={"layer": "UNCHANGED", "insert": (0, 20), "height": 2.5})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def test_bundled_font_file_exists_with_license():
    """同梱フォントと、再配布の条件であるライセンス文書がリポジトリに在る（消すとCloudで再発する）。"""
    assert (dxf_preview.BUNDLED_FONT_DIR / dxf_preview.BUNDLED_FONT_NAME).is_file()
    assert (dxf_preview.BUNDLED_FONT_DIR / "IPA_Font_License_Agreement_v1.0.txt").is_file()


def test_renders_japanese_text_when_no_system_fonts(no_system_fonts):
    """システムフォントが皆無でも例外にならず、同梱フォントで日本語を描画する。"""
    assert not no_system_fonts.has_font(dxf_preview.BUNDLED_FONT_NAME)  # 前提: 本当に空
    result = dxf_preview.render_layers_svg(_dxf_with_japanese_text())
    assert result.cjk_font == dxf_preview.BUNDLED_FONT_NAME
    assert not result.empty
    unchanged = next(layer for layer in result.layers if layer.name == "UNCHANGED")
    assert unchanged.inner_svg.count("<path") > 1  # 線1本より多い＝文字が描かれた
