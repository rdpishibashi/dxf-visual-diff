import streamlit as st
import os
import tempfile
import sys
from pathlib import Path

# utils モジュールをインポート可能にするためのパスの追加
current_dir = os.path.dirname(os.path.abspath(__file__))
utils_path = os.path.join(current_dir, 'utils')
sys.path.insert(0, utils_path)

from utils.compare_dxf import compare_dxf_files_and_generate_dxf
from utils.common_utils import save_uploadedfile, handle_error

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

def app():
    st.title('DXF図面 差分解析')
    st.write('複数のDXFファイルペアを比較し、差分をDXFフォーマットで出力します。')
    
    # プログラム説明
    with st.expander("ℹ️ プログラム説明", expanded=False):
        help_text = [
            "このツールは、複数のDXFファイルペアを比較し、各ペアごとに差分をDXFファイルとして出力します。",
            "",
            "**使用手順：**",
            "1. 各ファイルペアを登録してください（最大5ペア）",
            "2. 必要に応じてオプション設定を調整します",
            "3. 「DXF差分を比較」ボタンをクリックして処理を実行します",
            "",
            "**出力DXFファイルの内容：**",
            "- ADDED (デフォルト色: シアン): 比較対象ファイル(B)にのみ存在する要素",
            "- DELETED (デフォルト色: マゼンタ): 基準ファイル(A)にのみ存在する要素", 
            "- UNCHANGED (デフォルト色: 白/黒): 両方のファイルに存在し変更がない要素"
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
    
    # オプション設定
    with st.expander("オプション設定", expanded=False):
        col1, col2 = st.columns(2)
        
        with col1:
            # 許容誤差設定
            tolerance = st.number_input(
                "座標許容誤差", 
                min_value=1e-8, 
                max_value=1e-1, 
                value=0.01,
                format="%.8f",
                help="図面の位置座標の比較における許容誤差です。大きくすると微小な違いを無視します。"
            )
        
        with col2:
            st.write("**レイヤー色設定**")
            deleted_color = st.selectbox(
                "削除エンティティの色",
                options=[(1, "1 - 赤"), (2, "2 - 黄"), (3, "3 - 緑"), (4, "4 - シアン"), (5, "5 - 青"), (6, "6 - マゼンタ"), (7, "7 - 白/黒")],
                index=5,  # デフォルト: マゼンタ
                format_func=lambda x: x[1]
            )[0]
            
            added_color = st.selectbox(
                "追加エンティティの色",
                options=[(1, "1 - 赤"), (2, "2 - 黄"), (3, "3 - 緑"), (4, "4 - シアン"), (5, "5 - 青"), (6, "6 - マゼンタ"), (7, "7 - 白/黒")],
                index=3,  # デフォルト: シアン
                format_func=lambda x: x[1]
            )[0]
            
            unchanged_color = st.selectbox(
                "変更なしエンティティの色",
                options=[(1, "1 - 赤"), (2, "2 - 黄"), (3, "3 - 緑"), (4, "4 - シアン"), (5, "5 - 青"), (6, "6 - マゼンタ"), (7, "7 - 白/黒")],
                index=6,  # デフォルト: 白/黒
                format_func=lambda x: x[1]
            )[0]
    
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
                        
                        # DXF比較処理
                        result = compare_dxf_files_and_generate_dxf(
                            temp_file_a, 
                            temp_file_b, 
                            temp_output,
                            tolerance=tolerance,
                            deleted_color=deleted_color,
                            added_color=added_color,
                            unchanged_color=unchanged_color
                        )
                        
                        if result:
                            # 結果ファイルを読み込み
                            with open(temp_output, 'rb') as f:
                                dxf_data = f.read()
                            
                            results.append((
                                pair_name,
                                file_a.name,
                                file_b.name,
                                output_filename,
                                dxf_data,
                                True
                            ))
                        else:
                            results.append((
                                pair_name,
                                file_a.name,
                                file_b.name,
                                output_filename,
                                None,
                                False
                            ))
                    
                    # 結果をセッション状態に保存
                    st.session_state.processing_results = results
                    st.session_state.processing_settings = {
                        'added_color': added_color,
                        'deleted_color': deleted_color,
                        'unchanged_color': unchanged_color
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
            
            # ダウンロードボタンの表示
            st.subheader("差分解析結果")
            for pair_name, file_a_name, file_b_name, output_filename, dxf_data, success in results:
                if success and dxf_data:
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        st.write(f"**{pair_name}**: {file_a_name} ↔ {file_b_name}")
                    
                    with col2:
                        st.download_button(
                            label="ダウンロード",
                            data=dxf_data,
                            file_name=output_filename,
                            mime="application/dxf",
                            key=f"download_{pair_name}"
                        )
                elif not success:
                    st.error(f"**{pair_name}**: {file_a_name} ↔ {file_b_name} - 処理に失敗しました")
            
            # 新しい比較を開始するボタン
            if st.button("🔄 新しい比較を開始", key="restart_button"):
                # セッション状態をクリアして新しい比較を開始
                for key in list(st.session_state.keys()):
                    if key == 'processing_results' or key == 'processing_settings':
                        del st.session_state[key]
                st.rerun()
            
            # オプション設定の情報を表示
            if settings:
                st.info(f"""
                生成されたDXFファイルでは、以下のレイヤーで差分が表示されます：
                - ADDED (色{settings.get('added_color', 4)}): 比較対象ファイル(B)にのみ存在する要素
                - DELETED (色{settings.get('deleted_color', 6)}): 基準ファイル(A)にのみ存在する要素
                - UNCHANGED (色{settings.get('unchanged_color', 7)}): 両方のファイルに存在し変更がない要素
                """)
    else:
        st.warning("少なくとも1つのファイルペア（基準DXFファイル、比較対象DXFファイル）を登録してください。")

if __name__ == "__main__":
    app()