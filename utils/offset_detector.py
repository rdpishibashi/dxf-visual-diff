"""
オフセット補正の自動検出（モデル層）

2026-09-17新設。手動の「オフセット補正設定」UIに代わり、ファイルA・Bで一致しなかった
図形どうしから、位置に依存しない「形状」で対応付けを行い、複数のオフセット候補を
自動的に検出する。

設計方針:
- このモジュールは `utils/compare_dxf.py` に一切依存しない（循環import回避）。
  座標変換・署名生成・ハッシュ計算はすべて呼び出し側から関数として注入される。
- 純粋ロジックのみを持つため、合成データで単体テストしやすい。

アルゴリズムの概要（`detect_offsets()` のdocstring参照）:
1. 未一致のA・Bエンティティそれぞれについて、アンカー座標を原点へ移した状態の
   署名（＝位置に依存しない「形状キー」）を作る
2. 同じ形状キーを持つA×Bの全ペアについて、アンカー座標の差分（＝移動量）を
   オフセット候補として得票させる
3. 得票上位の候補について、実際に一致する図形の集合を求める
4. 一致件数の多い候補から貪欲に採用する。採用条件は
   「一致件数がしきい値以上」かつ「一致した図形の形状の種類数がしきい値以上」
   （後者は、罫線等の等間隔繰り返し形状が1ピッチずれて偶然一致する偽陽性を防ぐガード）
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from collections import defaultdict

# アンカー座標として使う座標属性（この順に最初に見つかったものを使う）。
# compare_dxf.py の SignatureGenerator.create_absolute_entity_signature() が
# 主要位置として拾う属性 ['insert', 'center', 'start', 'location'] と揃えている。
ANCHOR_ATTRIBUTES = ['insert', 'center', 'start', 'location']


@dataclass
class OffsetDetectionConfig:
    """オフセット自動検出のしきい値設定"""
    min_matches: int = 10
    min_distinct_shapes: int = 5
    max_offsets: int = 20
    max_candidates: int = 50
    max_instances_per_shape: int = 8


@dataclass
class DetectedOffset:
    """採用されたオフセット候補1件"""
    offset: Tuple[float, float]
    matched_b_hashes: Set[str] = field(default_factory=set)
    matched_a_hashes: Set[str] = field(default_factory=set)
    distinct_shapes: int = 0


def entity_anchor(absolute_entity: Dict) -> Optional[Tuple[float, float]]:
    """絶対座標エンティティからアンカー座標 (x, y) を取り出す。

    ANCHOR_ATTRIBUTES の順に最初に見つかった座標を使う。どれも無ければ
    LWPOLYLINEの最初の頂点、それも無ければ None（アンカーを持たないエンティティは
    候補生成の対象外になる）。
    """
    attrs = absolute_entity.get('attributes', {})
    for attr_name in ANCHOR_ATTRIBUTES:
        point = attrs.get(attr_name)
        if point is not None:
            return (float(point[0]), float(point[1]))
    vertices = attrs.get('vertices')
    if vertices:
        v = vertices[0]
        return (float(v[0]), float(v[1]))
    return None


def _build_shape_index(hashes: Set[str], entities: Dict, translate_fn: Callable,
                        signature_fn: Callable) -> Tuple[Dict[str, List[Tuple[float, float]]], Dict[str, str]]:
    """未一致エンティティ集合から、形状キー → アンカー座標リストの索引を作る。

    戻り値: (shape_key -> [anchor, ...], entity_hash -> shape_key)
    """
    index: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    shape_key_of: Dict[str, str] = {}
    for entity_hash in hashes:
        instances = entities.get(entity_hash)
        if not instances:
            continue
        absolute_entity = instances[0][1]['absolute_entity']
        anchor = entity_anchor(absolute_entity)
        if anchor is None:
            continue
        # アンカーを原点へ移した状態の署名 = 位置に依存しない形状キー
        normalized = translate_fn(absolute_entity, (-anchor[0], -anchor[1]))
        shape_key = signature_fn(normalized)
        index[shape_key].append(anchor)
        shape_key_of[entity_hash] = shape_key
    return index, shape_key_of


def _vote_candidate_offsets(idx_a: Dict[str, List[Tuple[float, float]]],
                             idx_b: Dict[str, List[Tuple[float, float]]],
                             tolerance: float, max_instances_per_shape: int) -> Dict[Tuple[float, float], int]:
    """同一形状キーのA×Bペアからオフセット候補を得票させる"""
    votes: Dict[Tuple[float, float], int] = defaultdict(int)
    half_tol = tolerance / 2
    for shape_key, b_anchors in idx_b.items():
        a_anchors = idx_a.get(shape_key)
        if not a_anchors:
            continue
        # 同一形状が多すぎる場合は対応付けが曖昧（組み合わせ爆発も防ぐ）
        if len(a_anchors) > max_instances_per_shape or len(b_anchors) > max_instances_per_shape:
            continue
        for ba in b_anchors:
            for aa in a_anchors:
                dx = round((aa[0] - ba[0]) / tolerance) * tolerance
                dy = round((aa[1] - ba[1]) / tolerance) * tolerance
                if abs(dx) < half_tol and abs(dy) < half_tol:
                    continue  # ゼロ近傍（＝実質オフセットなし）は候補にしない
                votes[(round(dx, 6), round(dy, 6))] += 1
    return votes


def detect_offsets(entities_a: Dict, entities_b: Dict,
                    unmatched_a_hashes: Set[str], unmatched_b_hashes: Set[str],
                    all_a_hashes: Set[str],
                    signature_fn: Callable[[Dict], str],
                    hash_fn: Callable[[Dict], Optional[str]],
                    entity_data_fn: Callable[[Dict], Optional[Dict]],
                    translate_fn: Callable[[Dict, Tuple[float, float]], Dict],
                    tolerance: float,
                    config: OffsetDetectionConfig) -> Tuple[List[DetectedOffset], int]:
    """複数のオフセット補正値を自動検出する。

    Args:
        entities_a / entities_b: extract_entities_from_doc() が返す
            {hash: [(location, virtual_entity), ...]} 形式の辞書
        unmatched_a_hashes / unmatched_b_hashes: 完全一致(common)を除いた
            A側・B側のハッシュ集合
        all_a_hashes: A側の全エンティティハッシュ集合（一致判定に使う）
        signature_fn: 絶対座標エンティティ dict を受け取り署名文字列を返す
            （SignatureGenerator.create_absolute_entity_signature 相当）
        hash_fn: entity_data dict を受け取りハッシュ文字列を返す
            （DiffAnalyzer.generate_enhanced_hash 相当）
        entity_data_fn: 絶対座標エンティティ dict を受け取り entity_data dict を返す
            （DiffAnalyzer.create_entity_data_from_absolute 相当）
        translate_fn: (absolute_entity, (dx, dy)) を受け取り平行移動後のコピーを返す
            （translate_absolute_entity 相当）
        tolerance: 座標の量子化単位
        config: しきい値設定

    Returns:
        (採用されたDetectedOffsetのリスト（一致件数降順）, しきい値未満で
         不採用になった候補数)
    """
    idx_a, _ = _build_shape_index(unmatched_a_hashes, entities_a, translate_fn, signature_fn)
    idx_b, shape_key_of_b = _build_shape_index(unmatched_b_hashes, entities_b, translate_fn, signature_fn)

    votes = _vote_candidate_offsets(idx_a, idx_b, tolerance, config.max_instances_per_shape)
    if not votes:
        return [], 0

    # 得票上位 max_candidates 個だけを検証対象にする。
    # 同数得票のタイブレークはオフセット値自体（座標なので決定的）で行う——
    # ハッシュ集合の走査順（PYTHONHASHSEED依存）にタイブレークを委ねると、
    # 同一入力でも実行のたびに上位境界の採否が変わり結果が非決定的になる
    # （2026-09-17、実データ検証中に発見: PYTHONHASHSEEDを変えるだけで
    # 採用オフセット数・UNCHANGED_OFFSET件数が変動する再現性バグがあった）。
    candidate_offsets = [off for off, _ in
                          sorted(votes.items(), key=lambda item: (-item[1], item[0]))[:config.max_candidates]]

    # 各候補について、未一致B全体に対する一致集合を1回だけ求めてキャッシュする。
    # (b_hash, a_hash) のペア集合を保持し、貪欲適用時は集合演算（積）だけで
    # 済ませる——remaining は unmatched_b_hashes の部分集合であり、一致判定は
    # エンティティ単体で決まる（他に何が残っているかに依存しない）ため、
    # matches(off, remaining) == matches(off, unmatched_b_hashes) & remaining が
    # 常に成り立つ。これにより貪欲ループでの再計算を避ける。
    full_matches: Dict[Tuple[float, float], Set[Tuple[str, str]]] = {}
    for offset in candidate_offsets:
        pairs: Set[Tuple[str, str]] = set()
        for b_hash in unmatched_b_hashes:
            instances = entities_b.get(b_hash)
            if not instances:
                continue
            absolute_entity = instances[0][1]['absolute_entity']
            shifted = translate_fn(absolute_entity, offset)
            entity_data = entity_data_fn(shifted)
            shifted_hash = hash_fn(entity_data) if entity_data else None
            if shifted_hash and shifted_hash in all_a_hashes:
                pairs.add((b_hash, shifted_hash))
        full_matches[offset] = pairs

    # 一致件数の多い候補から貪欲に適用（タイブレークは上と同じ理由でオフセット値自体）
    order = sorted(candidate_offsets, key=lambda off: (-len(full_matches[off]), off))

    adopted: List[DetectedOffset] = []
    rejected = 0
    remaining_b = set(unmatched_b_hashes)

    for offset in order:
        if len(adopted) >= config.max_offsets:
            break
        hit_pairs = {(bh, ah) for bh, ah in full_matches[offset] if bh in remaining_b}
        if not hit_pairs:
            continue
        matched_b = {bh for bh, _ in hit_pairs}
        matched_a = {ah for _, ah in hit_pairs}
        distinct_shapes = len({shape_key_of_b[bh] for bh in matched_b if bh in shape_key_of_b})

        if len(matched_b) >= config.min_matches and distinct_shapes >= config.min_distinct_shapes:
            adopted.append(DetectedOffset(
                offset=offset,
                matched_b_hashes=matched_b,
                matched_a_hashes=matched_a,
                distinct_shapes=distinct_shapes,
            ))
            remaining_b -= matched_b
        else:
            rejected += 1

    return adopted, rejected
