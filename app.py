import streamlit as st
import os
import tempfile
import sys
from pathlib import Path
import zipfile
from io import BytesIO

# utils モジュールをインポート可能にするためのパスの追加
current_dir = os.path.dirname(os.path.abspath(__file__))
utils_path = os.path.join(current_dir, 'utils')
sys.path.insert(0, utils_path)

from utils.compare_dxf import compare_dxf_files_and_generate_dxf
from utils.common_utils import save_uploadedfile, handle_error
from utils.offset_detector import OffsetDetectionConfig

from config import diff_config

st.set_page_config(
    page_title="DXF Visual Diff",
    page_icon="📊",
    layout="wide",
)

def generate_output_filename(file_a_name, file_b_name):
    """
    出力ファイル名を生成: (A filename)_vs_(B filename).dxf
    """
    # 拡張子を除いた基本ファイル名を取得
    file_a_base = Path(file_a_name).stem
    file_b_base = Path(file_b_name).stem

    return f"{file_a_base}_vs_{file_b_base}.dxf"

def create_zip_archive(results):
    """
    複数のDXFファイルをZIPアーカイブに圧縮
    """
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for pair_name, file_a_name, file_b_name, output_filename, dxf_data, success, _ in results:
            if success and dxf_data:
                # ZIPファイル内のファイル名を設定
                zip_file.writestr(output_filename, dxf_data)

    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def app():
    st.title('DXF Visual Diff Analyzer')
    st.write('複数のDXFファイルペアを比較し、差分をDXFフォーマットで出力します。')
    
    # プログラム説明
    with st.expander("ℹ️ プログラム説明", expanded=False):
        help_text = [
            "このツールは、複数のDXFファイルペアを比較し、各ペアごとに差分をDXFファイルとして出力します。",
            "",
            "**使用手順：**",
            "1. 各ファイルペアを登録してください（最大5ペア）",
            "2. 「DXF差分を比較」ボタンをクリックして処理を実行します",
            "",
            "**出力DXFファイルの内容（7レイヤー構成）：**",
            "外部CADソフトで開いた直後は「A_ALL」「B_ALL」の2枚だけが表示された状態になり、"
            "「A_ALL」で基準ファイル(A)の図面全体が、「B_ALL」で比較対象ファイル(B)の図面全体が、"
            "それぞれ変更箇所の色分けつきで再現されます"
            "（複数レイヤーを選択する手間を無くすための合成レイヤーです）。"
            "詳細を個別に確認したい場合は、以下のカテゴリ別レイヤー（既定では非表示）を"
            "手動でONにしてください。",
            "- A_DELETED (デフォルト色: マゼンタ): 基準ファイル(A)にのみ存在する要素",
            "- B_ADDED (デフォルト色: シアン): 比較対象ファイル(B)にのみ存在する要素",
            "- UNCHANGED (デフォルト色: 白/黒): 両方のファイルに存在し変更がない要素"
            "（A/Bで内容が同一のため1レイヤーに統合しています）",
            "- A_UNCHANGED_OFFSET (デフォルト色: 濃灰) / B_UNCHANGED_OFFSET (デフォルト色: 明灰): "
            "一部の図形だけが平行移動している場合に、その移動量（オフセット）を自動検出して"
            "一致とみなした要素（それぞれ基準ファイル(A)・比較対象ファイル(B)の座標で描画。"
            "検出されたオフセットは結果画面に一覧表示されます）",
            "- A_ALL / B_ALL: 上記カテゴリ別レイヤーのうち、それぞれファイルA/Bの再現に"
            "必要なもの（A_ALL = A_DELETED + UNCHANGED + A_UNCHANGED_OFFSET、"
            "B_ALL = B_ADDED + UNCHANGED + B_UNCHANGED_OFFSET）を1枚に複製した合成レイヤー"
        ]
        
        st.info("\n".join(help_text))
    
    # ファイルペア登録UI
    st.subheader("ファイルペア登録")
    st.write("最大5ペアのDXFファイルを登録できます")
    
    # セッション状態の初期化
    if 'file_pairs' not in st.session_state:
        st.session_state.file_pairs = []
        for i in range(5):  # 最大5ペア
            st.session_state.file_pairs.append({
                'fileA': None,
                'fileB': None,
                'name': f"Pair{i+1}"
            })
    
    # 各ペアの入力フォーム
    file_pairs_valid = []
    
    for i in range(5):  # 最大5ペア
        with st.expander(f"ファイルペア {i+1}", expanded=i==0):
            col1, col2, col3 = st.columns([2, 2, 1])
            
            with col1:
                uploaded_file_a = st.file_uploader(
                    f"基準DXFファイル (A) {i+1}", 
                    type="dxf", 
                    key=f"dxf_a_{i}"
                )
                if uploaded_file_a:
                    st.session_state.file_pairs[i]['fileA'] = uploaded_file_a
                
            with col2:
                uploaded_file_b = st.file_uploader(
                    f"比較対象DXFファイル (B) {i+1}", 
                    type="dxf", 
                    key=f"dxf_b_{i}"
                )
                if uploaded_file_b:
                    st.session_state.file_pairs[i]['fileB'] = uploaded_file_b
            
            with col3:
                pair_name = st.text_input(
                    "ペア名",
                    value=st.session_state.file_pairs[i]['name'],
                    key=f"pair_name_{i}"
                )
                st.session_state.file_pairs[i]['name'] = pair_name
            
            # 両方のファイルが選択されている場合、有効なペアとして追加
            if st.session_state.file_pairs[i]['fileA'] and st.session_state.file_pairs[i]['fileB']:
                output_filename = generate_output_filename(
                    st.session_state.file_pairs[i]['fileA'].name,
                    st.session_state.file_pairs[i]['fileB'].name
                )
                
                file_pairs_valid.append((
                    st.session_state.file_pairs[i]['fileA'],
                    st.session_state.file_pairs[i]['fileB'],
                    st.session_state.file_pairs[i]['name'],
                    output_filename
                ))
                
                # プレビュー表示
                st.success(f"Pair{i+1}: {st.session_state.file_pairs[i]['fileA'].name} と {st.session_state.file_pairs[i]['fileB'].name} を比較")
                st.info(f"出力ファイル名: {output_filename}")
    
    # 設定値はconfig.pyから取得（管理者用。UIには表示しない）
    tolerance = diff_config.DEFAULT_TOLERANCE
    deleted_color = diff_config.DEFAULT_DELETED_COLOR
    added_color = diff_config.DEFAULT_ADDED_COLOR
    unchanged_color = diff_config.DEFAULT_UNCHANGED_COLOR
    unchanged_offset_a_color = diff_config.DEFAULT_UNCHANGED_OFFSET_A_COLOR
    unchanged_offset_b_color = diff_config.DEFAULT_UNCHANGED_OFFSET_B_COLOR

    # オフセット補正の自動検出設定（2026-09-17新設。手動の「オフセット補正設定」UIは廃止）
    offset_detection = None
    if diff_config.AUTO_OFFSET_DETECTION:
        offset_detection = OffsetDetectionConfig(
            min_matches=diff_config.AUTO_OFFSET_MIN_MATCHES,
            min_distinct_shapes=diff_config.AUTO_OFFSET_MIN_DISTINCT_SHAPES,
            max_offsets=diff_config.AUTO_OFFSET_MAX_OFFSETS,
            max_candidates=diff_config.AUTO_OFFSET_MAX_CANDIDATES,
            max_instances_per_shape=diff_config.AUTO_OFFSET_MAX_INSTANCES_PER_SHAPE,
            compact_min_matches=diff_config.AUTO_OFFSET_COMPACT_MIN_MATCHES,
            compact_min_distinct_shapes=diff_config.AUTO_OFFSET_COMPACT_MIN_DISTINCT_SHAPES,
            compact_max_span=diff_config.AUTO_OFFSET_COMPACT_MAX_SPAN,
        )

    if file_pairs_valid:
        try:
            # ファイルが選択されたら処理ボタンを表示
            if st.button("DXF差分を比較", disabled=len(file_pairs_valid) == 0):
                # 全てのファイルペアを処理
                with st.spinner(f'{len(file_pairs_valid)}ペアのDXFファイルを比較中...'):
                    results = []
                    temp_files_to_cleanup = []

                    for file_a, file_b, pair_name, output_filename in file_pairs_valid:
                        # 一時ファイルに保存
                        temp_file_a = save_uploadedfile(file_a)
                        temp_file_b = save_uploadedfile(file_b)
                        temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf").name

                        temp_files_to_cleanup.extend([temp_file_a, temp_file_b, temp_output])

                        # DXF比較処理（オフセット補正は自動検出。config.py の
                        # AUTO_OFFSET_DETECTION で無効化しない限り常に適用される）
                        success, entity_counts = compare_dxf_files_and_generate_dxf(
                            temp_file_a,
                            temp_file_b,
                            temp_output,
                            tolerance=tolerance,
                            deleted_color=deleted_color,
                            added_color=added_color,
                            unchanged_color=unchanged_color,
                            unchanged_offset_a_color=unchanged_offset_a_color,
                            unchanged_offset_b_color=unchanged_offset_b_color,
                            offset_detection=offset_detection
                        )

                        if success:
                            # 結果ファイルを読み込み
                            with open(temp_output, 'rb') as f:
                                dxf_data = f.read()

                            results.append((
                                pair_name,
                                file_a.name,
                                file_b.name,
                                output_filename,
                                dxf_data,
                                True,
                                entity_counts
                            ))
                        else:
                            results.append((
                                pair_name,
                                file_a.name,
                                file_b.name,
                                output_filename,
                                None,
                                False,
                                None
                            ))

                    # 結果をセッション状態に保存
                    st.session_state.processing_results = results
                    st.session_state.processing_settings = {
                        'added_color': added_color,
                        'deleted_color': deleted_color,
                        'unchanged_color': unchanged_color,
                        'unchanged_offset_a_color': unchanged_offset_a_color,
                        'unchanged_offset_b_color': unchanged_offset_b_color
                    }
                
                # 一時ファイルの削除
                for temp_file in temp_files_to_cleanup:
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
        
        except Exception as e:
            handle_error(e)
        
        # セッション状態に保存された結果を表示
        if 'processing_results' in st.session_state and st.session_state.processing_results:
            results = st.session_state.processing_results
            settings = st.session_state.get('processing_settings', {})

            # 結果サマリーの表示
            successful_pairs = sum(1 for r in results if r[5])
            total_pairs = len(results)
            
            if successful_pairs == total_pairs:
                st.success(f"全{total_pairs}ペアのDXF比較が完了しました")
            elif successful_pairs > 0:
                st.warning(f"{successful_pairs}/{total_pairs}ペアのDXF比較が完了しました。一部のペアで処理に失敗しました。")
            else:
                st.error("全てのペアで処理に失敗しました")
            
            # ダウンロード方法の選択
            st.subheader("差分解析結果")

            # 成功したペアの数をカウント
            successful_results = [r for r in results if r[5] and r[4]]

            if len(successful_results) > 1:
                download_method = st.radio(
                    "ダウンロード方法を選択",
                    options=["個別にダウンロード", "ZIPアーカイブとしてダウンロード"],
                    horizontal=True,
                    key="download_method"
                )
            else:
                download_method = "個別にダウンロード"

            # ZIPダウンロードボタン（複数ファイルが成功した場合のみ表示）
            if download_method == "ZIPアーカイブとしてダウンロード" and len(successful_results) > 1:
                zip_data = create_zip_archive(results)
                st.download_button(
                    label="📦 全ての結果をZIPでダウンロード",
                    data=zip_data,
                    file_name="dxf_diff_results.zip",
                    mime="application/zip",
                    key="download_all_zip",
                    type="primary"
                )
                st.write("---")

            # 個別ダウンロードボタンまたはリスト表示
            for pair_name, file_a_name, file_b_name, output_filename, dxf_data, success, entity_counts in results:
                if success and dxf_data:
                    if download_method == "個別にダウンロード":
                        col1, col2 = st.columns([3, 1])

                        with col1:
                            st.write(f"**{pair_name}**: {file_a_name} ↔ {file_b_name}")
                            # エンティティ数の表示（2026-09-18、A側/B側で分けて表示。
                            # unchanged_offset_a_entities/total_a_entities はオフセット
                            # 補正未使用・6レイヤー化前の旧セッション結果には存在しない
                            # 可能性があるため .get() で読む）
                            if entity_counts:
                                unchanged_offset_b = entity_counts.get('unchanged_offset_entities', 0)
                                unchanged_offset_a = entity_counts.get('unchanged_offset_a_entities', 0)
                                offset_a_caption = f" / オフセット一致 {unchanged_offset_a}" if unchanged_offset_a > 0 else ""
                                offset_b_caption = f" / オフセット一致 {unchanged_offset_b}" if unchanged_offset_b > 0 else ""
                                total_a = entity_counts.get('total_a_entities')
                                total_a_caption = f"（計 {total_a}）" if total_a is not None else ""
                                st.caption(
                                    f" A側: 削除 {entity_counts['deleted_entities']} / "
                                    f"変更なし {entity_counts['unchanged_entities']}"
                                    f"{offset_a_caption}{total_a_caption}"
                                )
                                st.caption(
                                    f" B側: 追加 {entity_counts['added_entities']} / "
                                    f"変更なし {entity_counts['unchanged_entities']}"
                                    f"{offset_b_caption}（計 {entity_counts['total_entities']}）"
                                )

                        with col2:
                            st.download_button(
                                label="ダウンロード",
                                data=dxf_data,
                                file_name=output_filename,
                                mime="application/dxf",
                                key=f"download_{pair_name}"
                            )

                        # 検出されたオフセットの一覧を表示（自動検出未使用・
                        # 検出0件の場合は表示しない）
                        detected_offsets = entity_counts.get('detected_offsets', []) if entity_counts else []
                        if detected_offsets:
                            rejected_count = entity_counts.get('rejected_offset_candidates', 0)
                            with st.expander(f"🔍 検出されたオフセット（{len(detected_offsets)}個）", expanded=False):
                                for d in detected_offsets:
                                    dx, dy = d['offset']
                                    # span/compact は自動検出未使用の旧セッション結果には
                                    # 存在しない可能性があるため .get() で読む
                                    span = d.get('span', 0.0)
                                    compact_note = "（コンパクト救済）" if d.get('compact') else ""
                                    st.caption(
                                        f"({dx:.2f}, {dy:.2f}) ｜ 一致: {d['matches']}件 ｜ "
                                        f"形状の種類: {d['shapes']}種類 ｜ "
                                        f"広がり: {span:.1f}{compact_note}"
                                    )
                                if rejected_count > 0:
                                    st.caption(f"※ しきい値未満で不採用の候補: {rejected_count}個")
                    else:
                        # ZIPダウンロード時はファイルリストのみ表示
                        entity_info = ""
                        if entity_counts:
                            entity_info = f" (差分: {entity_counts['diff_entities']}件)"
                        st.write(f"✅ **{pair_name}**: {file_a_name} ↔ {file_b_name} → `{output_filename}`{entity_info}")
                elif not success:
                    st.error(f"❌ **{pair_name}**: {file_a_name} ↔ {file_b_name} - 処理に失敗しました")

            # 新しい比較を開始するボタン
            if st.button("🔄 新しい比較を開始", key="restart_button"):
                # セッション状態をクリアして新しい比較を開始
                for key in list(st.session_state.keys()):
                    if key in ['processing_results', 'processing_settings']:
                        del st.session_state[key]
                st.rerun()
            
            # レイヤー構成の凡例を表示
            if settings:
                offset_a_color = settings.get('unchanged_offset_a_color', 8)
                offset_b_color = settings.get('unchanged_offset_b_color', 9)
                st.info(f"""
                生成されたDXFファイルは7レイヤー構成です。開いた直後は「A_ALL」「B_ALL」の
                2枚だけが表示され、「A_ALL」で基準ファイル(A)の図面全体が、「B_ALL」で
                比較対象ファイル(B)の図面全体が、それぞれ変更箇所の色分けつきで再現されます
                （複数レイヤーを選択する手間を無くすための合成レイヤーです）。
                以下のカテゴリ別レイヤーは既定では非表示で、詳細を確認したいときに
                手動でONにできます：
                - A_DELETED (色{settings.get('deleted_color', 6)}): 基準ファイル(A)にのみ存在する要素
                - B_ADDED (色{settings.get('added_color', 4)}): 比較対象ファイル(B)にのみ存在する要素
                - UNCHANGED (色{settings.get('unchanged_color', 7)}): 両方のファイルに
                  存在し変更がない要素（A/Bで内容が同一のため1レイヤーに統合）
                - A_UNCHANGED_OFFSET (色{offset_a_color}) / B_UNCHANGED_OFFSET (色{offset_b_color}):
                  自動検出されたオフセットで一致した要素（検出0件のペアでは生成されません。それぞれ
                  基準ファイル(A)・比較対象ファイル(B)の座標で描画。検出内容は各ペアの
                  「🔍 検出されたオフセット」から確認できます）
                - A_ALL / B_ALL: 上記のうちファイルA/Bの再現に必要なものを1枚に複製した合成レイヤー
                """)
    else:
        st.warning("少なくとも1つのファイルペア（基準DXFファイル、比較対象DXFファイル）を登録してください。")

if __name__ == "__main__":
    app()