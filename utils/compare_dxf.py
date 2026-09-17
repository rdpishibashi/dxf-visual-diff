import ezdxf
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional, Any
from decimal import Decimal, getcontext
import logging
import numpy as np
import tempfile
import os
import gc

from .common_utils import is_invisible
from .offset_detector import detect_offsets, OffsetDetectionConfig

# 高精度計算設定
getcontext().prec = 50

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 平行移動（オフセット）の対象となる座標属性。
# EntityExpander._transform_coordinate_attributes() と translate_absolute_entity()
# の両方から参照する（取りこぼし防止のため一元管理）。
COORDINATE_ATTRIBUTES = ['insert', 'center', 'start', 'end', 'location', 'base_point']


class ToleranceConfig:
    """許容誤差設定クラス"""
    
    def __init__(self, base_tolerance: float = 0.01):
        self.base_tolerance = base_tolerance
        self.coordinate_tolerance = base_tolerance
        self.connection_tolerance = base_tolerance * 0.1
        self.text_position_tolerance = base_tolerance * 2
        self.angle_tolerance = 0.1
        self.length_tolerance = base_tolerance
        
    def get_tolerance_for_entity(self, entity_type: str, attribute: str = None) -> float:
        """エンティティタイプ・属性に応じた許容誤差を取得"""
        if entity_type in ['TEXT', 'MTEXT', 'ATTRIB']:
            return self.text_position_tolerance
        elif entity_type == 'POINT' or (attribute and 'connection' in attribute.lower()):
            return self.connection_tolerance
        elif attribute and any(angle_attr in attribute for angle_attr in ['angle', 'rotation']):
            return self.angle_tolerance
        else:
            return self.coordinate_tolerance


class CoordinateTransformer:
    """座標変換専用クラス"""
    
    def __init__(self, tolerance_config: ToleranceConfig, debug: bool = False):
        self.tolerance_config = tolerance_config
        self.debug = debug
        
    def normalize_coordinate_precise(self, value: float, tolerance: float) -> float:
        """高精度座標正規化"""
        if tolerance <= 0:
            return value
        
        try:
            decimal_value = Decimal(str(value))
            decimal_tolerance = Decimal(str(tolerance))
            normalized = (decimal_value / decimal_tolerance).quantize(Decimal('1')) * decimal_tolerance
            return float(normalized)
        except Exception:
            return round(value / tolerance) * tolerance
    
    def normalize_coordinate_with_context(self, coord: Any, entity_type: str, 
                                        attribute: str = None) -> Any:
        """コンテキストを考慮した座標正規化"""
        tolerance = self.tolerance_config.get_tolerance_for_entity(entity_type, attribute)
        
        if hasattr(coord, 'x') and hasattr(coord, 'y') and hasattr(coord, 'z'):
            return tuple(
                self.normalize_coordinate_precise(float(c), tolerance) 
                for c in [coord.x, coord.y, coord.z]
            )
        elif isinstance(coord, (tuple, list)):
            return tuple(
                self.normalize_coordinate_precise(float(c), tolerance) 
                for c in coord
            )
        elif isinstance(coord, (int, float)):
            return self.normalize_coordinate_precise(float(coord), tolerance)
        
        return coord
    
    def create_transformation_matrix(self, insert_entity) -> np.ndarray:
        """INSERTエンティティから4x4変換行列を作成"""
        try:
            # 挿入点
            insert_point = getattr(insert_entity.dxf, 'insert', (0, 0, 0))
            if hasattr(insert_point, 'x'):
                tx, ty, tz = float(insert_point.x), float(insert_point.y), float(getattr(insert_point, 'z', 0))
            else:
                coords = list(insert_point) if insert_point else [0, 0, 0]
                tx = float(coords[0]) if len(coords) > 0 else 0.0
                ty = float(coords[1]) if len(coords) > 1 else 0.0
                tz = float(coords[2]) if len(coords) > 2 else 0.0
            
            # 回転角度
            rotation_deg = getattr(insert_entity.dxf, 'rotation', 0.0)
            rotation = math.radians(float(rotation_deg))
            
            # スケール
            xscale = float(getattr(insert_entity.dxf, 'xscale', 1.0))
            yscale = float(getattr(insert_entity.dxf, 'yscale', 1.0))
            zscale = float(getattr(insert_entity.dxf, 'zscale', 1.0))
            
            # 変換行列を作成
            scale_matrix = np.array([
                [xscale, 0.0, 0.0, 0.0],
                [0.0, yscale, 0.0, 0.0],
                [0.0, 0.0, zscale, 0.0],
                [0.0, 0.0, 0.0, 1.0]
            ], dtype=np.float64)
            
            cos_r, sin_r = math.cos(rotation), math.sin(rotation)
            rotation_matrix = np.array([
                [cos_r, -sin_r, 0.0, 0.0],
                [sin_r, cos_r, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0]
            ], dtype=np.float64)
            
            translation_matrix = np.array([
                [1.0, 0.0, 0.0, tx],
                [0.0, 1.0, 0.0, ty],
                [0.0, 0.0, 1.0, tz],
                [0.0, 0.0, 0.0, 1.0]
            ], dtype=np.float64)
            
            return translation_matrix @ rotation_matrix @ scale_matrix
            
        except Exception as e:
            logger.warning(f"Error creating transformation matrix: {e}")
            return np.eye(4, dtype=np.float64)
    
    def transform_point(self, point: Tuple[float, float, float], 
                       transform_matrix: np.ndarray) -> Tuple[float, float, float]:
        """点を変換行列で変換"""
        try:
            if len(point) == 2:
                point = (point[0], point[1], 0.0)
            elif len(point) < 2:
                point = (point[0] if point else 0.0, 0.0, 0.0)
            
            homogeneous_point = np.array([point[0], point[1], point[2], 1.0])
            transformed = transform_matrix @ homogeneous_point
            return (float(transformed[0]), float(transformed[1]), float(transformed[2]))
            
        except Exception:
            return point
    
    def extract_scale_factors(self, transform_matrix: np.ndarray) -> Tuple[float, float, float]:
        """変換行列からスケールファクターを抽出"""
        try:
            scale_x = math.sqrt(transform_matrix[0, 0]**2 + transform_matrix[1, 0]**2)
            scale_y = math.sqrt(transform_matrix[0, 1]**2 + transform_matrix[1, 1]**2)
            scale_z = math.sqrt(transform_matrix[0, 2]**2 + transform_matrix[1, 2]**2 + transform_matrix[2, 2]**2)
            return (scale_x, scale_y, scale_z)
        except Exception:
            return (1.0, 1.0, 1.0)


class EntityExpander:
    """INSERTエンティティ展開専用クラス"""

    def __init__(self, transformer: CoordinateTransformer, debug: bool = False,
                 global_offset: Optional[Tuple[float, float]] = None):
        self.transformer = transformer
        self.debug = debug
        self.global_offset = global_offset  # グローバルオフセット (dx, dy)
        self.excluded_attributes = {
            'handle', 'owner', 'reactors', 'dictionary', 'extension_dict',
            'objectid', 'uuid', 'app_data', 'doc', 'entitydb', 'is_alive',
            'is_virtual', 'is_copy', 'soft_pointer_ids', 'hard_pointer_ids'
        }
        # レイヤー名 → 表示されるか（off でも frozen でもない）のマップ。
        # expand_insert_entities() の先頭で対象 doc から構築する
        # （DXF-diff-manager 2026-09-16の横展開に伴い移植）。
        self._layer_visible: Dict[str, bool] = {}

    def _build_layer_visibility(self, doc) -> None:
        """レイヤー名 → 可視（off でも frozen でもない）かのマップを構築する。

        off/frozen なレイヤー上のエンティティは図面に表示されない。ビジュアル差分は
        「見えている図形」を比較対象とすべきなので、これらを抽出段階で除外するために使う。
        重なった旧タイトルブロックや改訂履歴メモ等が off/frozen レイヤーに残っている
        DXF で、新旧同一の不可視テキストが UNCHANGED として差分DXFに描画される不具合の対策
        （DXF-diff-manager で実データ EE2505-611-79B_vs_79A にて確認: ブロック
        JZB_0004 の MTEXT 'EE2505-611-57B' 等が off+frozen レイヤー上にあった）。
        """
        vis = {}
        try:
            for layer in doc.layers:
                try:
                    vis[layer.dxf.name] = not (layer.is_off() or layer.is_frozen())
                except Exception:
                    vis[layer.dxf.name] = True  # 判定不能時は表示扱い（安全側）
        except Exception:
            pass
        self._layer_visible = vis

    def _is_layer_visible(self, layer_name) -> bool:
        """レイヤーが表示される（off/frozen でない）か。未知レイヤー・'0' は表示扱い。

        '0' はブロック参照のレイヤーを継承するため、ここでは表示扱いにし、
        参照元 INSERT のレイヤー可視性は呼び出し側で別途判定する。
        """
        if not layer_name or layer_name == '0':
            return True
        return self._layer_visible.get(layer_name, True)

    def safe_get_dxf_attributes(self, entity) -> Dict:
        """安全なDXF属性取得"""
        try:
            all_attrs = entity.dxf.all_existing_dxf_attribs()
            clean_attrs = {k: v for k, v in all_attrs.items() 
                          if k not in self.excluded_attributes}
            
            # LWPOLYLINE特別処理
            if entity.dxftype() == 'LWPOLYLINE':
                vertices = self._extract_lwpolyline_vertices(entity)
                if vertices:
                    clean_attrs['vertices'] = vertices
            
            return clean_attrs
            
        except Exception as e:
            if self.debug:
                logger.debug(f"Error getting attributes for {entity.dxftype()}: {e}")
            return {}
    
    def _extract_lwpolyline_vertices(self, entity) -> List[Tuple[float, float]]:
        """LWPOLYLINE頂点情報を抽出"""
        vertices = []
        
        # 複数の方法で頂点を取得を試行
        methods = [
            lambda: list(entity.get_points()) if hasattr(entity, 'get_points') else [],
            lambda: [(v.x, v.y) for v in entity.vertices] if hasattr(entity, 'vertices') else [],
            lambda: [(v[0], v[1]) for v in entity.vertices] if hasattr(entity, 'vertices') else []
        ]
        
        for method in methods:
            try:
                points = method()
                if points:
                    vertices = [(float(p[0]), float(p[1])) for p in points if len(p) >= 2]
                    break
            except Exception:
                continue
        
        return vertices
    
    def transform_entity_to_absolute(self, entity, transform_matrix: np.ndarray) -> Optional[Dict]:
        """エンティティを絶対座標に変換"""
        try:
            entity_type = entity.dxftype()
            clean_attrs = self.safe_get_dxf_attributes(entity)
            transformed_attrs = clean_attrs.copy()
            
            # スケールファクターを抽出
            scale_x, scale_y, scale_z = self.transformer.extract_scale_factors(transform_matrix)
            is_scaled = not all(math.isclose(s, 1.0, rel_tol=1e-6) for s in [scale_x, scale_y, scale_z])
            
            # 座標属性を変換
            self._transform_coordinate_attributes(clean_attrs, transformed_attrs, transform_matrix)
            
            # サイズ関連属性の変換
            if is_scaled:
                self._transform_size_attributes(entity_type, clean_attrs, transformed_attrs, 
                                              scale_x, scale_y, scale_z)
            
            # テキスト内容の取得
            text_content = getattr(entity, 'text', None) or getattr(entity.dxf, 'text', None)
            
            return {
                'dxftype': entity_type,
                'attributes': transformed_attrs,
                'text_content': text_content,
                'is_transformed': True,
                'original_entity_id': id(entity),
                'scale_factors': (scale_x, scale_y, scale_z) if is_scaled else None
            }
            
        except Exception as e:
            logger.warning(f"Error transforming entity {entity.dxftype()}: {e}")
            return None
    
    def _apply_global_offset(self, point: Tuple[float, float, float]) -> Tuple[float, float, float]:
        """グローバルオフセットを適用"""
        if self.global_offset is None:
            return point

        dx, dy = self.global_offset
        return (point[0] + dx, point[1] + dy, point[2] if len(point) > 2 else 0.0)

    def _transform_coordinate_attributes(self, clean_attrs: Dict, transformed_attrs: Dict,
                                       transform_matrix: np.ndarray):
        """座標属性を変換"""
        for attr_name in COORDINATE_ATTRIBUTES:
            if attr_name in clean_attrs:
                original_point = clean_attrs[attr_name]
                try:
                    if hasattr(original_point, 'x'):
                        point_tuple = (original_point.x, original_point.y,
                                     getattr(original_point, 'z', 0.0))
                    else:
                        point_tuple = tuple(original_point)

                    transformed_point = self.transformer.transform_point(point_tuple, transform_matrix)

                    # グローバルオフセットを適用
                    transformed_point = self._apply_global_offset(transformed_point)

                    transformed_attrs[attr_name] = transformed_point

                except Exception:
                    pass
        
        # ELLIPSE major_axis ベクトルの変換（方向ベクトルなので原点からの変換）
        if 'major_axis' in clean_attrs:
            original_major_axis = clean_attrs['major_axis']
            try:
                if hasattr(original_major_axis, 'x'):
                    axis_tuple = (original_major_axis.x, original_major_axis.y, 
                                getattr(original_major_axis, 'z', 0.0))
                else:
                    axis_tuple = tuple(original_major_axis) if original_major_axis else (1, 0, 0)
                
                # ベクトル変換（平行移動は適用しない）
                axis_homogeneous = np.array([axis_tuple[0], axis_tuple[1], axis_tuple[2], 0.0])
                transformed_axis = transform_matrix @ axis_homogeneous
                transformed_attrs['major_axis'] = (float(transformed_axis[0]), 
                                                 float(transformed_axis[1]), 
                                                 float(transformed_axis[2]))
                
            except Exception:
                pass
        
        # LWPOLYLINE頂点の変換
        if 'vertices' in clean_attrs:
            original_vertices = clean_attrs['vertices']
            transformed_vertices = []

            for vertex in original_vertices:
                if len(vertex) >= 2:
                    vertex_3d = (vertex[0], vertex[1], 0.0) if len(vertex) == 2 else vertex
                    transformed_vertex = self.transformer.transform_point(vertex_3d, transform_matrix)

                    # グローバルオフセットを適用
                    transformed_vertex = self._apply_global_offset(transformed_vertex)

                    transformed_vertices.append(transformed_vertex[:2])

            transformed_attrs['vertices'] = transformed_vertices
    
    def _transform_size_attributes(self, entity_type: str, clean_attrs: Dict, 
                                 transformed_attrs: Dict, scale_x: float, scale_y: float, scale_z: float):
        """サイズ関連属性の変換"""
        if entity_type in ['CIRCLE', 'ARC'] and 'radius' in clean_attrs:
            avg_scale = (scale_x + scale_y) / 2.0
            transformed_attrs['radius'] = clean_attrs['radius'] * avg_scale
        
        if entity_type == 'ELLIPSE':
            # ELLIPSE の major_axis ベクトルにスケールを適用
            if 'major_axis' in clean_attrs:
                major_axis = clean_attrs['major_axis']
                if hasattr(major_axis, 'x'):
                    scaled_major_axis = (major_axis.x * scale_x, major_axis.y * scale_y, 
                                       getattr(major_axis, 'z', 0.0) * scale_z)
                else:
                    coords = list(major_axis) if major_axis else [1, 0, 0]
                    scaled_major_axis = (coords[0] * scale_x, 
                                       coords[1] * scale_y if len(coords) > 1 else 0.0,
                                       coords[2] * scale_z if len(coords) > 2 else 0.0)
                transformed_attrs['major_axis'] = scaled_major_axis
            # ratio は変更不要（major と minor の比は保持）
        
        if entity_type in ['TEXT', 'MTEXT', 'ATTRIB'] and 'height' in clean_attrs:
            transformed_attrs['height'] = clean_attrs['height'] * scale_y
    
    def expand_insert_entities(self, doc, doc_label: str) -> List[Dict]:
        """INSERTエンティティを展開して絶対座標エンティティリストを作成
        （ブロック内にさらにINSERTがある「ネストINSERT」も再帰的に展開する。
        2026-09-16、DXF-diff-managerから移植。以前は1段階のみの展開だったため、
        二重INSERT構造を持つブロック内の図形が展開されず、代替テキスト
        `[INSERT]` として出力されていた——ユーザー報告により発覚。実データ
        EE3273-039-06B_vs_EE4153-039-06B で、JZB_0008/0009/0019〜0027 の
        11ブロックがそれぞれ内部に2個のINSERT（匿名ブロック）を持ち、
        計22個の記号が欠落していたことを確認）"""
        expanded_entities = []

        # off/frozen レイヤー上のエンティティ（＝図面に表示されない）を除外するための
        # レイヤー可視性マップを構築する（DXF-diff-manager 2026-09-16の横展開に伴い移植）。
        self._build_layer_visibility(doc)

        msp = doc.modelspace()
        for entity in msp:
            entity_type = entity.dxftype()

            if entity_type == 'INSERT':
                # INSERT自身が off/frozen レイヤーにあるか、invisible属性（非表示設定）
                # が立っていれば、中身ごと丸ごと除外する（is_invisibleのdocstring参照）。
                if not self._is_layer_visible(getattr(entity.dxf, 'layer', '0')) or is_invisible(entity):
                    continue
                try:
                    transform_matrix = self.transformer.create_transformation_matrix(entity)
                    self._expand_insert_recursive(doc, entity, transform_matrix, expanded_entities)
                except Exception as e:
                    logger.warning(f"Error expanding INSERT {entity.dxf.name}: {e}")

            elif entity_type != 'ATTDEF':
                # 直接エンティティ（off/frozen レイヤー・invisible属性なら表示されないので除外）
                if not self._is_layer_visible(getattr(entity.dxf, 'layer', '0')) or is_invisible(entity):
                    continue
                identity_matrix = np.eye(4)
                absolute_entity = self.transform_entity_to_absolute(entity, identity_matrix)
                if absolute_entity:
                    absolute_entity['is_direct_modelspace'] = True
                    expanded_entities.append(absolute_entity)

        return expanded_entities

    def _expand_insert_recursive(self, doc, insert_entity, transform_matrix: np.ndarray,
                                  expanded_entities: List[Dict], depth: int = 0,
                                  max_depth: int = 20) -> None:
        """1つのINSERTエンティティをブロック内容に展開し、結果を expanded_entities に追加する。
        ブロック内にネストしたINSERTがあれば、親の変換行列と合成した行列で再帰展開する。
        depth は循環参照（ブロックが自分自身を間接的に参照する等）による無限再帰を防ぐガード。"""
        if depth > max_depth:
            logger.warning(
                f"INSERT nesting exceeded max depth ({max_depth}) at block "
                f"'{insert_entity.dxf.name}', stopping recursion")
            return

        block_name = insert_entity.dxf.name
        if block_name not in doc.blocks:
            return
        block = doc.blocks[block_name]

        for block_entity in block:
            if block_entity.dxftype() == 'ATTDEF':
                continue

            # ブロック定義内エンティティが明示的な off/frozen レイヤー上にある場合、
            # 参照元 INSERT のレイヤーに関わらず図面に表示されないため除外する。
            # レイヤー'0'はINSERTのレイヤーを継承する（呼び出し前にINSERT側の
            # 可視性は確認済みなので表示扱いでよい）。invisible属性についても同様に
            # ここで除外する（ネストしたINSERT自身がinvisibleな場合もこのチェックで
            # 再帰展開ごと止まる）。
            if not self._is_layer_visible(getattr(block_entity.dxf, 'layer', '0')) or is_invisible(block_entity):
                continue

            if block_entity.dxftype() == 'INSERT':
                nested_local_matrix = self.transformer.create_transformation_matrix(block_entity)
                nested_matrix = transform_matrix @ nested_local_matrix
                self._expand_insert_recursive(
                    doc, block_entity, nested_matrix, expanded_entities, depth + 1, max_depth)
                continue

            absolute_entity = self.transform_entity_to_absolute(block_entity, transform_matrix)
            if absolute_entity:
                absolute_entity['insert_info'] = {
                    'block_name': block_name,
                    'insert_point': tuple(insert_entity.dxf.insert),
                    'rotation': getattr(insert_entity.dxf, 'rotation', 0.0),
                    'scale': (
                        getattr(insert_entity.dxf, 'xscale', 1.0),
                        getattr(insert_entity.dxf, 'yscale', 1.0),
                        getattr(insert_entity.dxf, 'zscale', 1.0)
                    )
                }
                expanded_entities.append(absolute_entity)

        # ATTRIB処理
        if hasattr(insert_entity, 'attribs'):
            for attrib in insert_entity.attribs:
                if is_invisible(attrib):
                    continue
                identity_matrix = np.eye(4)
                absolute_attrib = self.transform_entity_to_absolute(attrib, identity_matrix)
                if absolute_attrib:
                    absolute_attrib['insert_info'] = {
                        'block_name': block_name,
                        'insert_point': tuple(insert_entity.dxf.insert),
                        'is_insert_attrib': True
                    }
                    expanded_entities.append(absolute_attrib)


def _translate_point(point, dx: float, dy: float):
    """座標点（2要素/3要素のタプル・リスト）を (dx, dy) だけ平行移動する"""
    if len(point) >= 3:
        return (point[0] + dx, point[1] + dy, point[2])
    return (point[0] + dx, point[1] + dy)


def translate_absolute_entity(absolute_entity: Dict, offset: Tuple[float, float]) -> Dict:
    """
    展開済みの絶対座標エンティティ（EntityExpander の出力）を、
    ベクトル (dx, dy) だけ平行移動した新しい辞書として返す（元は変更しない）。

    EntityExpander._apply_global_offset() が transform_point() の結果に
    offset を加算するのと数値的に等価な操作を、展開後のデータに対して
    事後的に行う。DXF の再パース・再展開（INSERT展開）は不要。

    平行移動の対象は COORDINATE_ATTRIBUTES に列挙された座標属性と
    'vertices'（LWPOLYLINE の頂点群）のみ。'major_axis'（方向ベクトル）や
    'radius'/'height'/'rotation' 等のスカラー値は対象外
    （EntityExpander._apply_global_offset と同じ扱い）。
    """
    dx, dy = offset
    translated = dict(absolute_entity)
    attrs = dict(absolute_entity.get('attributes', {}))

    for attr_name in COORDINATE_ATTRIBUTES:
        if attr_name in attrs and attrs[attr_name] is not None:
            attrs[attr_name] = _translate_point(attrs[attr_name], dx, dy)

    if 'vertices' in attrs and attrs['vertices']:
        attrs['vertices'] = [_translate_point(v, dx, dy) for v in attrs['vertices']]

    translated['attributes'] = attrs
    return translated


class SignatureGenerator:
    """エンティティ署名生成専用クラス"""
    
    def __init__(self, transformer: CoordinateTransformer, debug: bool = False):
        self.transformer = transformer
        self.debug = debug
    
    def create_absolute_entity_signature(self, absolute_entity: Dict) -> str:
        """絶対座標エンティティの署名生成"""
        try:
            entity_type = absolute_entity['dxftype']
            attrs = absolute_entity['attributes']
            
            signature_parts = [entity_type]
            
            # 主要位置情報
            position = None
            for pos_attr in ['insert', 'center', 'start', 'location']:
                if pos_attr in attrs:
                    position = attrs[pos_attr]
                    break
            
            if position:
                normalized_pos = self.transformer.normalize_coordinate_with_context(position, entity_type)
                signature_parts.append(f"pos_{normalized_pos}")
            
            # INSERT位置情報は除外（絶対座標変換済みのため不要）
            # 同じ最終座標・属性の entities は INSERT 元に関係なく同一として扱う
            
            # テキスト内容
            text_content = absolute_entity.get('text_content')
            if text_content and text_content.strip():
                clean_text = text_content.strip().replace('\n', '').replace('\r', '')
                signature_parts.append(f"text_{clean_text}")
            
            # ATTRIB固有情報
            if entity_type == 'ATTRIB':
                attrib_tag = absolute_entity.get('attrib_tag', '')
                signature_parts.append(f"tag_{attrib_tag}")
            
            # 重要な属性
            self._add_important_attributes(signature_parts, attrs, entity_type, absolute_entity)
            
            # ジオメトリ詳細
            self._add_geometry_details(signature_parts, entity_type, attrs)
            
            return "_".join(str(p) for p in signature_parts)
            
        except Exception as e:
            if self.debug:
                logger.debug(f"Error creating signature: {e}")
            return f"{entity_type}_error_{id(absolute_entity)}"
    
    def _add_important_attributes(self, signature_parts: List, attrs: Dict, 
                                entity_type: str, absolute_entity: Dict):
        """重要な属性を署名に追加"""
        important_attrs = ['color', 'height', 'radius', 'start_angle', 'end_angle']
        
        for attr_name in important_attrs:
            if attr_name in attrs:
                value = attrs[attr_name]
                if isinstance(value, (int, float)):
                    if attr_name in ['height', 'radius'] and absolute_entity.get('scale_factors'):
                        tolerance = self.transformer.tolerance_config.get_tolerance_for_entity(
                            entity_type, attr_name) * 2
                    else:
                        tolerance = self.transformer.tolerance_config.get_tolerance_for_entity(
                            entity_type, attr_name)
                    value = self.transformer.normalize_coordinate_precise(float(value), tolerance)
                signature_parts.append(f"{attr_name}_{value}")
        
        # 回転角度の特別処理
        if 'rotation' in attrs:
            rotation = attrs['rotation']
            if isinstance(rotation, (int, float)):
                normalized_rotation = float(rotation) % (2 * math.pi)
                angle_tolerance = self.transformer.tolerance_config.angle_tolerance
                normalized_rotation = self.transformer.normalize_coordinate_precise(
                    normalized_rotation, math.radians(angle_tolerance))
                signature_parts.append(f"rotation_{normalized_rotation}")
    
    def _add_geometry_details(self, signature_parts: List, entity_type: str, attrs: Dict):
        """ジオメトリ詳細を署名に追加"""
        if entity_type == 'LINE' and 'start' in attrs and 'end' in attrs:
            start = self.transformer.normalize_coordinate_with_context(attrs['start'], entity_type)
            end = self.transformer.normalize_coordinate_with_context(attrs['end'], entity_type)
            signature_parts.append(f"line_{start}_{end}")
        
        elif entity_type == 'CIRCLE' and 'center' in attrs and 'radius' in attrs:
            center = self.transformer.normalize_coordinate_with_context(attrs['center'], entity_type)
            radius = self.transformer.normalize_coordinate_precise(
                attrs['radius'], self.transformer.tolerance_config.length_tolerance)
            signature_parts.append(f"circle_{center}_{radius}")
        
        elif entity_type == 'ARC' and 'center' in attrs:
            center = self.transformer.normalize_coordinate_with_context(attrs['center'], entity_type)
            radius = self.transformer.normalize_coordinate_precise(
                attrs.get('radius', 0), self.transformer.tolerance_config.length_tolerance)
            start_angle = self.transformer.normalize_coordinate_precise(
                attrs.get('start_angle', 0), 
                math.radians(self.transformer.tolerance_config.angle_tolerance))
            end_angle = self.transformer.normalize_coordinate_precise(
                attrs.get('end_angle', 0), 
                math.radians(self.transformer.tolerance_config.angle_tolerance))
            signature_parts.append(f"arc_{center}_{radius}_{start_angle}_{end_angle}")
        
        elif entity_type == 'ELLIPSE' and 'center' in attrs:
            center = self.transformer.normalize_coordinate_with_context(attrs['center'], entity_type)
            major_axis = self.transformer.normalize_coordinate_with_context(
                attrs.get('major_axis', (1, 0, 0)), entity_type)
            ratio = self.transformer.normalize_coordinate_precise(
                attrs.get('ratio', 1.0), self.transformer.tolerance_config.length_tolerance)
            start_param = self.transformer.normalize_coordinate_precise(
                attrs.get('start_param', 0.0), 
                math.radians(self.transformer.tolerance_config.angle_tolerance))
            end_param = self.transformer.normalize_coordinate_precise(
                attrs.get('end_param', 2 * math.pi), 
                math.radians(self.transformer.tolerance_config.angle_tolerance))
            signature_parts.append(f"ellipse_{center}_{major_axis}_{ratio}_{start_param}_{end_param}")
        
        elif entity_type == 'LWPOLYLINE' and 'vertices' in attrs:
            vertices = attrs['vertices']
            if vertices:
                normalized_vertices = []
                for vertex in vertices[:5]:  # 最初の5頂点のみ
                    if len(vertex) >= 2:
                        norm_vertex = self.transformer.normalize_coordinate_with_context(
                            (vertex[0], vertex[1]), entity_type)
                        normalized_vertices.append(norm_vertex)
                if normalized_vertices:
                    signature_parts.append(f"lwpoly_vertices_{normalized_vertices}")


class DiffAnalyzer:
    """差分検出専用クラス"""
    
    def __init__(self, signature_generator: SignatureGenerator, debug: bool = False):
        self.signature_generator = signature_generator
        self.debug = debug
    
    def generate_enhanced_hash(self, entity_data: Dict) -> Optional[str]:
        """改善されたハッシュ生成"""
        if entity_data is None:
            return None
        
        try:
            signature = entity_data.get('absolute_signature', '')
            if signature:
                hash_value = hashlib.sha256(signature.encode('utf-8')).hexdigest()
            else:
                json_str = json.dumps(entity_data, sort_keys=True, ensure_ascii=False, 
                                    separators=(',', ':'), default=str)
                hash_value = hashlib.sha256(json_str.encode('utf-8')).hexdigest()
            
            return hash_value
            
        except Exception as e:
            logger.warning(f"Failed to generate hash: {e}")
            return None
    
    def create_entity_data_from_absolute(self, absolute_entity: Dict) -> Optional[Dict]:
        """絶対座標エンティティからハッシュ用データ作成"""
        try:
            absolute_signature = self.signature_generator.create_absolute_entity_signature(absolute_entity)
            
            entity_data = {
                'dxftype': absolute_entity['dxftype'],
                'absolute_signature': absolute_signature,
                'attributes': absolute_entity['attributes'],
                'text_content': absolute_entity.get('text_content'),
                'is_transformed': absolute_entity.get('is_transformed', False)
            }
            
            if absolute_entity['dxftype'] == 'ATTRIB':
                if 'attrib_tag' in absolute_entity:
                    entity_data['attrib_tag'] = absolute_entity['attrib_tag']
            
            if 'insert_info' in absolute_entity:
                entity_data['insert_info'] = absolute_entity['insert_info']
            
            self._extract_geometry_details(absolute_entity, entity_data)
            
            return entity_data
            
        except Exception as e:
            logger.warning(f"Error creating entity data: {e}")
            return None
    
    def _extract_geometry_details(self, absolute_entity: Dict, entity_data: Dict):
        """ジオメトリ詳細情報の抽出"""
        try:
            entity_type = absolute_entity['dxftype']
            attrs = absolute_entity['attributes']
            
            if entity_type == 'LINE' and 'start' in attrs and 'end' in attrs:
                start = self.signature_generator.transformer.normalize_coordinate_with_context(
                    attrs['start'], entity_type)
                end = self.signature_generator.transformer.normalize_coordinate_with_context(
                    attrs['end'], entity_type)
                entity_data['line_geometry'] = {'start': start, 'end': end}
            
            elif entity_type == 'CIRCLE' and 'center' in attrs and 'radius' in attrs:
                center = self.signature_generator.transformer.normalize_coordinate_with_context(
                    attrs['center'], entity_type)
                entity_data['circle_geometry'] = {'center': center, 'radius': attrs['radius']}
            
            elif entity_type == 'ARC' and 'center' in attrs:
                center = self.signature_generator.transformer.normalize_coordinate_with_context(
                    attrs['center'], entity_type)
                entity_data['arc_geometry'] = {
                    'center': center, 
                    'radius': attrs.get('radius', 0),
                    'start_angle': attrs.get('start_angle', 0), 
                    'end_angle': attrs.get('end_angle', 0)
                }
            
            elif entity_type == 'ELLIPSE' and 'center' in attrs:
                center = self.signature_generator.transformer.normalize_coordinate_with_context(
                    attrs['center'], entity_type)
                major_axis = self.signature_generator.transformer.normalize_coordinate_with_context(
                    attrs.get('major_axis', (1, 0, 0)), entity_type)
                entity_data['ellipse_geometry'] = {
                    'center': center,
                    'major_axis': major_axis,
                    'ratio': attrs.get('ratio', 1.0),
                    'start_param': attrs.get('start_param', 0.0),
                    'end_param': attrs.get('end_param', 2 * math.pi)
                }
                
        except Exception:
            pass
    
    def extract_entities_from_doc(self, doc, doc_label: str, expander: EntityExpander) -> Tuple[Dict[str, List], Dict[str, Dict], Dict[str, Set[str]]]:
        """ドキュメントからエンティティを抽出"""
        entities_by_hash = defaultdict(list)
        hash_to_entity_data = {}
        hash_to_locations = defaultdict(set)
        
        absolute_entities = expander.expand_insert_entities(doc, doc_label)
        
        for absolute_entity in absolute_entities:
            try:
                entity_data = self.create_entity_data_from_absolute(absolute_entity)
                if entity_data:
                    entity_hash = self.generate_enhanced_hash(entity_data)
                    if entity_hash:
                        if absolute_entity.get('is_direct_modelspace'):
                            location = 'modelspace'
                        else:
                            insert_info = absolute_entity.get('insert_info', {})
                            block_name = insert_info.get('block_name', 'unknown')
                            location = f"expanded_from_{block_name}"
                        
                        virtual_entity = {
                            'data': entity_data,
                            'absolute_entity': absolute_entity
                        }
                        
                        entities_by_hash[entity_hash].append((location, virtual_entity))
                        hash_to_entity_data[entity_hash] = entity_data
                        hash_to_locations[entity_hash].add(location)
                        
            except Exception as e:
                logger.warning(f"Error processing entity: {e}")
        
        return entities_by_hash, hash_to_entity_data, hash_to_locations


class LayerConfig:
    """レイヤー設定クラス"""
    
    def __init__(self, deleted_color: int = 6, added_color: int = 4, unchanged_color: int = 7,
                 unchanged_offset_color: int = 8):
        self.layer_settings = {
            'DELETED': {
                'name': 'DELETED',
                'color': deleted_color,  # デフォルト: マゼンタ
                'description': 'Entities present in file A but not in file B'
            },
            'ADDED': {
                'name': 'ADDED',
                'color': added_color,  # デフォルト: シアン
                'description': 'Entities present in file B but not in file A'
            },
            'UNCHANGED': {
                'name': 'UNCHANGED',
                'color': unchanged_color,  # デフォルト: 白/黒
                'description': 'Entities present in both files'
            },
            'UNCHANGED_OFFSET': {
                'name': 'UNCHANGED_OFFSET',
                'color': unchanged_offset_color,  # デフォルト: 灰
                'description': 'Entities matched after offset compensation (drawn at file B coordinates)'
            }
        }
    
    def get_layer_name(self, diff_type: str) -> str:
        """差分タイプからレイヤー名を取得"""
        return self.layer_settings.get(diff_type.upper(), {}).get('name', '0')
    
    def get_layer_color(self, diff_type: str) -> int:
        """差分タイプからレイヤー色を取得"""
        return self.layer_settings.get(diff_type.upper(), {}).get('color', 256)


class OutputGenerator:
    """出力生成専用クラス"""
    
    def __init__(self, transformer: CoordinateTransformer, layer_config: LayerConfig, debug: bool = False):
        self.transformer = transformer
        self.layer_config = layer_config
        self.debug = debug
        self.excluded_attributes = {
            'handle', 'owner', 'reactors', 'dictionary', 'extension_dict',
            'objectid', 'uuid', 'app_data', 'doc', 'entitydb', 'is_alive', 
            'is_virtual', 'is_copy', 'soft_pointer_ids', 'hard_pointer_ids'
        }
    
    def create_entity_from_absolute(self, absolute_entity: Dict, target_space, layer_name: str, layer_color: int) -> bool:
        """絶対座標エンティティから実際のDXFエンティティを作成（レイヤー指定）"""
        try:
            entity_type = absolute_entity['dxftype']
            attrs = absolute_entity['attributes']
            
            dxfattribs = {k: v for k, v in attrs.items() 
                         if k not in self.excluded_attributes and v is not None}
            
            # レイヤーと色を設定
            dxfattribs['layer'] = layer_name
            dxfattribs['color'] = layer_color
            
            if entity_type == 'LINE':
                start = attrs.get('start', (0, 0, 0))
                end = attrs.get('end', (1, 1, 0))
                target_space.add_line(start=start, end=end, dxfattribs=dxfattribs)
                
            elif entity_type == 'CIRCLE':
                center = attrs.get('center', (0, 0, 0))
                radius = attrs.get('radius', 1.0)
                target_space.add_circle(center=center, radius=radius, dxfattribs=dxfattribs)
                
            elif entity_type == 'ARC':
                center = attrs.get('center', (0, 0, 0))
                radius = attrs.get('radius', 1.0)
                start_angle = attrs.get('start_angle', 0.0)
                end_angle = attrs.get('end_angle', 90.0)
                target_space.add_arc(center=center, radius=radius,
                                   start_angle=start_angle, end_angle=end_angle,
                                   dxfattribs=dxfattribs)
                
            elif entity_type == 'ELLIPSE':
                center = attrs.get('center', (0, 0, 0))
                major_axis = attrs.get('major_axis', (1, 0, 0))
                ratio = attrs.get('ratio', 1.0)
                start_param = attrs.get('start_param', 0.0)
                end_param = attrs.get('end_param', 2 * math.pi)
                
                # ELLIPSE パラメータの検証と修正
                if ratio <= 0:
                    ratio = 1.0
                    logger.warning(f"Invalid ELLIPSE ratio {attrs.get('ratio')}, using default 1.0")
                
                # major_axis がゼロベクトルかチェック
                if isinstance(major_axis, (list, tuple)) and len(major_axis) >= 2:
                    if abs(major_axis[0]) < 1e-10 and abs(major_axis[1]) < 1e-10:
                        major_axis = (1, 0, 0)
                        logger.warning("Zero major_axis detected, using default (1,0,0)")
                
                try:
                    target_space.add_ellipse(center=center, major_axis=major_axis,
                                           ratio=ratio, start_param=start_param, 
                                           end_param=end_param, dxfattribs=dxfattribs)
                except Exception as ellipse_error:
                    logger.warning(f"Failed to create ELLIPSE: {ellipse_error}")
                    logger.warning(f"ELLIPSE params - center: {center}, major_axis: {major_axis}, ratio: {ratio}")
                    # フォールバック: 円として作成
                    try:
                        # major_axis の長さを半径として使用
                        if isinstance(major_axis, (list, tuple)) and len(major_axis) >= 2:
                            radius = math.sqrt(major_axis[0]**2 + major_axis[1]**2)
                        else:
                            radius = 1.0
                        target_space.add_circle(center=center, radius=radius, dxfattribs=dxfattribs)
                        logger.info(f"ELLIPSE converted to CIRCLE with radius {radius}")
                    except Exception as circle_error:
                        logger.error(f"Failed to create fallback CIRCLE for ELLIPSE: {circle_error}")
                        return False
                
            elif entity_type == 'TEXT':
                text_content = absolute_entity.get('text_content', '')
                insert_pos = attrs.get('insert', (0, 0, 0))
                text_attrs = dxfattribs.copy()
                text_attrs['insert'] = insert_pos
                target_space.add_text(text=text_content, dxfattribs=text_attrs)
                
            elif entity_type == 'MTEXT':
                text_content = absolute_entity.get('text_content', '')
                insert_pos = attrs.get('insert', (0, 0, 0))
                text_attrs = dxfattribs.copy()
                text_attrs['insert'] = insert_pos
                target_space.add_mtext(text=text_content, dxfattribs=text_attrs)
                
            elif entity_type == 'ATTRIB':
                text_content = absolute_entity.get('text_content', '')
                attrib_tag = absolute_entity.get('attrib_tag', '')
                insert_pos = attrs.get('insert', (0, 0, 0))
                
                display_text = text_content if text_content else f"[{attrib_tag}]"
                text_height = dxfattribs.get('height', 2.5)
                
                target_space.add_text(
                    text=display_text,
                    dxfattribs={
                        'layer': layer_name,
                        'insert': insert_pos,
                        'height': text_height,
                        'rotation': dxfattribs.get('rotation', 0.0),
                        'color': layer_color,
                        'style': dxfattribs.get('style', 'Standard')
                    }
                )
                
            elif entity_type == 'POINT':
                location = attrs.get('location', (0, 0, 0))
                target_space.add_point(location=location, dxfattribs=dxfattribs)
            
            elif entity_type == 'LWPOLYLINE':
                vertices = attrs.get('vertices', [])
                if vertices:
                    vertex_points = [(v[0], v[1]) for v in vertices if len(v) >= 2]
                    if vertex_points:
                        new_entity = target_space.add_lwpolyline(points=vertex_points)
                        new_entity.dxf.layer = layer_name
                        new_entity.dxf.color = layer_color
                        if len(vertex_points) >= 3:
                            new_entity.close()
            
            else:
                # サポートされていないエンティティ
                insert_pos = attrs.get('insert', attrs.get('center', (0, 0, 0)))
                target_space.add_text(
                    text=f"[{entity_type}]",
                    dxfattribs={
                        'layer': layer_name, 
                        'insert': insert_pos,
                        'height': 2.5,
                        'color': layer_color
                    }
                )
            
            return True
                
        except Exception as e:
            if self.debug:
                logger.debug(f"Error creating entity {entity_type}: {e}")
            return False
    
    def _ensure_japanese_text_compatibility(self, output_file: str):
        """日本語テキストの互換性を確保"""
        try:
            # DXF R2018+ はネイティブでUTF-8をサポートするため、
            # ファイルがUTF-8で正しく保存されていることを確認
            with open(output_file, 'rb') as f:
                content = f.read()
            
            # UTF-8として読み込み、正常に読み込めることを確認
            try:
                text_content = content.decode('utf-8')
                # UTF-8で正常に読み込める場合は何もしない
                logger.info("DXF file successfully uses UTF-8 encoding for Japanese text")
                return
            except UnicodeDecodeError:
                # UTF-8で読めない場合のみ修正を試行
                logger.warning("DXF file encoding issue detected, attempting to fix")
                
                # エラー許容でUTF-8として読み込み
                text_content = content.decode('utf-8', errors='replace')
                
                # UTF-8で再保存
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(text_content)
                    
        except Exception as e:
            logger.warning(f"Error ensuring Japanese text compatibility: {e}")
            # エラーの場合は元のファイルをそのまま使用
    
    def create_diff_dxf(self, entities_a: Dict, entities_b: Dict,
                        deleted_hashes: Set[str], added_hashes: Set[str],
                        common_hashes: Set[str], output_file: str,
                        unchanged_offset_hashes: Optional[Set[str]] = None):
        """差分DXFファイルを作成"""
        try:
            # R2018以降でより良いUnicode対応
            new_doc = ezdxf.new('R2018', setup=True)
            msp = new_doc.modelspace()

            # レイヤーを作成
            layers = new_doc.layers
            diff_types = ['DELETED', 'ADDED', 'UNCHANGED']
            # UNCHANGED_OFFSET はオフセット補正で使われた場合のみレイヤーを作る
            # （オフセット未使用時に空レイヤーを増やさないため）
            if unchanged_offset_hashes:
                diff_types.append('UNCHANGED_OFFSET')
            for diff_type in diff_types:
                layer_name = self.layer_config.get_layer_name(diff_type)
                layer_color = self.layer_config.get_layer_color(diff_type)
                layer = layers.new(layer_name)
                layer.color = layer_color

            # DELETED エンティティを追加
            layer_name = self.layer_config.get_layer_name('DELETED')
            layer_color = self.layer_config.get_layer_color('DELETED')
            
            for entity_hash in deleted_hashes:
                if entity_hash in entities_a:
                    for location, virtual_entity in entities_a[entity_hash]:
                        absolute_entity = virtual_entity['absolute_entity']
                        self.create_entity_from_absolute(absolute_entity, msp, layer_name, layer_color)
                        break  # 最初のインスタンスのみ
            
            # ADDED エンティティを追加
            layer_name = self.layer_config.get_layer_name('ADDED')
            layer_color = self.layer_config.get_layer_color('ADDED')
            
            for entity_hash in added_hashes:
                if entity_hash in entities_b:
                    for location, virtual_entity in entities_b[entity_hash]:
                        absolute_entity = virtual_entity['absolute_entity']
                        self.create_entity_from_absolute(absolute_entity, msp, layer_name, layer_color)
                        break  # 最初のインスタンスのみ
            
            # UNCHANGED エンティティを追加
            layer_name = self.layer_config.get_layer_name('UNCHANGED')
            layer_color = self.layer_config.get_layer_color('UNCHANGED')
            
            for entity_hash in common_hashes:
                if entity_hash in entities_a:
                    for location, virtual_entity in entities_a[entity_hash]:
                        absolute_entity = virtual_entity['absolute_entity']
                        self.create_entity_from_absolute(absolute_entity, msp, layer_name, layer_color)
                        break  # 最初のインスタンスのみ

            # UNCHANGED_OFFSET エンティティを追加
            # （オフセット補正で初めて一致した要素。file B の座標で描画する）
            if unchanged_offset_hashes:
                layer_name = self.layer_config.get_layer_name('UNCHANGED_OFFSET')
                layer_color = self.layer_config.get_layer_color('UNCHANGED_OFFSET')

                for entity_hash in unchanged_offset_hashes:
                    if entity_hash in entities_b:
                        for location, virtual_entity in entities_b[entity_hash]:
                            absolute_entity = virtual_entity['absolute_entity']
                            self.create_entity_from_absolute(absolute_entity, msp, layer_name, layer_color)
                            break  # 最初のインスタンスのみ

            # DXFファイルを保存（UTF-8エンコーディングで日本語テキストを保持）
            new_doc.saveas(output_file)
            
            # 日本語テキストの互換性確保
            self._ensure_japanese_text_compatibility(output_file)
            return True
            
        except Exception as e:
            logger.error(f"Error creating diff DXF file {output_file}: {e}")
            return False


def compare_dxf_files_and_generate_dxf(file_a: str, file_b: str, output_file: str,
                                       tolerance: float = 0.01,
                                       deleted_color: int = 6,
                                       added_color: int = 4,
                                       unchanged_color: int = 7,
                                       unchanged_offset_color: int = 8,
                                       offset_b: Optional[Tuple[float, float]] = None,
                                       offset_detection: Optional[OffsetDetectionConfig] = None) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    DXFファイル比較メイン処理（Streamlit用インターフェース）

    オフセット補正は和集合型: 補正なしで一致した要素は従来どおり UNCHANGED の
    まま残り、補正して初めて一致した要素だけが別レイヤー UNCHANGED_OFFSET
    （file B の座標で描画）に追加される。ファイルBは常に生の座標で展開されるため、
    ADDED/DELETED の座標にオフセットは適用されない。

    オフセット値は2通りの与え方があり、両方指定した場合は両方が適用される
    （和集合）:
    - `offset_b`: 単一のオフセット値を明示的に指定する（従来からの経路）
    - `offset_detection`: 複数のオフセット値を自動検出する（2026-09-17新設。
      `utils/offset_detector.py` 参照）。しきい値を満たした候補だけが採用される

    Args:
        file_a: 基準DXFファイルパス
        file_b: 比較対象DXFファイルパス
        output_file: 出力DXFファイルパス
        tolerance: 座標許容誤差
        deleted_color: 削除エンティティの色（デフォルト: 6=マゼンタ）
        added_color: 追加エンティティの色（デフォルト: 4=シアン）
        unchanged_color: 変更なしエンティティの色（デフォルト: 7=白/黒）
        unchanged_offset_color: オフセット補正で一致したエンティティの色（デフォルト: 8=灰）
        offset_b: ファイルBとの一致判定に使うオフセット (dx, dy) のタプル (オプション)。
            座標のtolerance格子（既定0.05）の倍数でない場合、丸め先が1格子ずれて
            一致し損ねる要素が出ることがある。
        offset_detection: 複数オフセットの自動検出を有効にする場合、
            `OffsetDetectionConfig` を渡す（オプション。`None` なら自動検出しない）。

    Returns:
        Tuple[bool, Optional[Dict[str, Any]]]: (成功フラグ, エンティティ数情報)
            エンティティ数情報は以下のキーを含む辞書:
                - deleted_entities: 削除されたエンティティ数
                - added_entities: 追加されたエンティティ数
                - unchanged_entities: 変更なしエンティティ数（オフセット無しで一致）
                - unchanged_offset_entities: オフセット補正で一致したエンティティ数
                  （offset_b・offset_detection 両方の一致分を合算）
                - diff_entities: 差分エンティティ数（削除+追加）
                - total_entities: 総エンティティ数（unchanged_offset_entities を含む）
                - detected_offsets: 自動検出で採用されたオフセットのリスト
                  （[{'offset': (dx, dy), 'matches': int, 'shapes': int}, ...]、
                  一致件数降順。offset_detection未指定時は空リスト）
                - rejected_offset_candidates: しきい値未満で不採用になった
                  候補オフセット数（offset_detection未指定時は0）
    """
    try:
        # 設定の初期化
        tolerance_config = ToleranceConfig(tolerance)
        transformer = CoordinateTransformer(tolerance_config, debug=False)
        expander_a = EntityExpander(transformer, debug=False, global_offset=None)
        # ファイルBは常に生の座標で展開する（和集合型: オフセットは一致判定にのみ使う。
        # B全体をあらかじめ平行移動する旧・置き換え型の挙動はここでは行わない）
        expander_b = EntityExpander(transformer, debug=False, global_offset=None)
        signature_generator = SignatureGenerator(transformer, debug=False)
        diff_analyzer = DiffAnalyzer(signature_generator, debug=False)
        layer_config = LayerConfig(deleted_color, added_color, unchanged_color, unchanged_offset_color)
        output_generator = OutputGenerator(transformer, layer_config, debug=False)

        # DXFファイル読み込み
        doc_a = ezdxf.readfile(file_a)
        doc_b = ezdxf.readfile(file_b)

        # エンティティ抽出（両ファイルとも生の座標で展開）
        entities_a, data_a, locations_a = diff_analyzer.extract_entities_from_doc(
            doc_a, "A", expander_a)
        entities_b, data_b, locations_b = diff_analyzer.extract_entities_from_doc(
            doc_b, "B", expander_b)

        # 差分計算（第1パス: オフセット無しでの完全一致）
        hashes_a = set(entities_a.keys())
        hashes_b = set(entities_b.keys())

        common_hashes = hashes_a & hashes_b

        # 第2パス（和集合型オフセット補正）:
        # 完全一致しなかった B 側の要素を (dx, dy) だけ平行移動して再ハッシュし、
        # A 側と一致するものを「オフセット一致」として UNCHANGED_OFFSET に振り分ける。
        # DXFの再パース・再展開は行わず、展開済みエンティティ（B）を平行移動するのみ
        # （EntityExpander が offset 適用済みで展開するのと数値的に等価）。
        offset_matched_b_hashes: Set[str] = set()
        matched_a_hashes_by_offset: Set[str] = set()

        has_offset = offset_b is not None and (offset_b[0] != 0 or offset_b[1] != 0)
        if has_offset:
            for b_hash in hashes_b - common_hashes:
                instances = entities_b.get(b_hash)
                if not instances:
                    continue
                absolute_entity = instances[0][1]['absolute_entity']
                shifted_entity = translate_absolute_entity(absolute_entity, offset_b)
                shifted_data = diff_analyzer.create_entity_data_from_absolute(shifted_entity)
                shifted_hash = diff_analyzer.generate_enhanced_hash(shifted_data)
                if shifted_hash and shifted_hash in hashes_a:
                    offset_matched_b_hashes.add(b_hash)
                    matched_a_hashes_by_offset.add(shifted_hash)

        # 第3パス（複数オフセットの自動検出。2026-09-17新設）:
        # offset_b の手動指定で一致しなかった残りの未一致エンティティに対して、
        # utils/offset_detector.py で複数のオフセット候補を自動検出する。
        # offset_b と offset_detection は併用可能（和集合）。
        detected_offsets_info: List[Dict[str, Any]] = []
        rejected_offset_candidates = 0

        if offset_detection is not None:
            unmatched_a_for_detection = hashes_a - common_hashes - matched_a_hashes_by_offset
            unmatched_b_for_detection = hashes_b - common_hashes - offset_matched_b_hashes

            detected, rejected_offset_candidates = detect_offsets(
                entities_a, entities_b,
                unmatched_a_for_detection, unmatched_b_for_detection, hashes_a,
                signature_fn=signature_generator.create_absolute_entity_signature,
                hash_fn=diff_analyzer.generate_enhanced_hash,
                entity_data_fn=diff_analyzer.create_entity_data_from_absolute,
                translate_fn=translate_absolute_entity,
                tolerance=tolerance,
                config=offset_detection,
            )
            for detected_offset in detected:
                offset_matched_b_hashes |= detected_offset.matched_b_hashes
                matched_a_hashes_by_offset |= detected_offset.matched_a_hashes
                detected_offsets_info.append({
                    'offset': detected_offset.offset,
                    'matches': len(detected_offset.matched_b_hashes),
                    'shapes': detected_offset.distinct_shapes,
                })

        deleted_hashes = hashes_a - common_hashes - matched_a_hashes_by_offset
        added_hashes = hashes_b - common_hashes - offset_matched_b_hashes

        # エンティティ数を計算
        deleted_count = len(deleted_hashes)
        added_count = len(added_hashes)
        unchanged_count = len(common_hashes)
        unchanged_offset_count = len(offset_matched_b_hashes)
        diff_count = deleted_count + added_count
        total_count = deleted_count + added_count + unchanged_count + unchanged_offset_count

        entity_counts = {
            'deleted_entities': deleted_count,
            'added_entities': added_count,
            'unchanged_entities': unchanged_count,
            'unchanged_offset_entities': unchanged_offset_count,
            'diff_entities': diff_count,
            'total_entities': total_count,
            'detected_offsets': detected_offsets_info,
            'rejected_offset_candidates': rejected_offset_candidates,
        }

        # 差分DXFファイル生成
        success = output_generator.create_diff_dxf(
            entities_a, entities_b, deleted_hashes, added_hashes, common_hashes, output_file,
            unchanged_offset_hashes=offset_matched_b_hashes)

        # メモリ解放: 大きなデータ構造を削除
        del doc_a
        del doc_b
        del entities_a
        del entities_b
        del data_a
        del data_b
        del locations_a
        del locations_b
        del deleted_hashes
        del added_hashes
        del common_hashes
        del offset_matched_b_hashes
        del matched_a_hashes_by_offset
        # ガベージコレクションを実行
        gc.collect()

        return success, entity_counts if success else None

    except Exception as e:
        logger.error(f"DXF comparison error: {e}")
        # エラー時もメモリ解放
        gc.collect()
        return False, None