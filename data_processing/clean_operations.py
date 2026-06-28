#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merge "edit + cursor + edit (+ ...)" sequences by micro-step applying each edit patch
within the SAME context window. No expand. No line-gap filling. No extra markers.

Key properties:
- strict alternation: edit -> cursor -> edit -> ... -> edit
- same file path required
- (optional) time threshold / line distance threshold checks
- merge produces ONE edit_text op with:
  - timestamp = first edit timestamp
  - endTimestamp = last edit endTimestamp
  - readableDetails based on last edit readableDetails, but with:
    - context_content replaced by final window text with ONE accumulated FT tag set
    - (optional) context_start_line/end_line kept as last edit's (should be same across edits)
"""

import json
import re
from typing import List, Dict, Optional, Tuple


# ----------------------------
# Parsing helpers
# ----------------------------

def extract_file_path(readable_details: str) -> Optional[str]:
    m = re.search(r"<path>([^<]+)</path>", readable_details)
    return m.group(1) if m else None


def extract_context_range(readable_details: str) -> Tuple[Optional[int], Optional[int]]:
    sm = re.search(r"<context_start_line>(\d+)</context_start_line>", readable_details)
    em = re.search(r"<context_end_line>(\d+)</context_end_line>", readable_details)
    s = int(sm.group(1)) if sm else None
    e = int(em.group(1)) if em else None
    return s, e


def extract_context_content(readable_details: str) -> Optional[str]:
    m = re.search(r"<context_content>\s*(.*?)\s*</context_content>", readable_details, flags=re.DOTALL)
    return m.group(1) if m else None


# ----------------------------
# Reconstruct before/after window text
# ----------------------------

def reconstruct_text_before_operation(readable_details: str) -> Optional[str]:
    """
    Build the window text BEFORE operation from context_content:
    - remove FT_insert blocks (inserted new text)
    - unwrap FT_delete blocks (deleted old text becomes present again)
    """
    ctx = extract_context_content(readable_details)
    if ctx is None:
        return None
    text_before = re.sub(r"<FT_insert>.*?</FT_insert>", "", ctx, flags=re.DOTALL)
    text_before = re.sub(r"<FT_delete>(.*?)</FT_delete>", r"\1", text_before, flags=re.DOTALL)
    return text_before


def reconstruct_text_after_operation(readable_details: str) -> Optional[str]:
    """
    Build the window text AFTER operation from context_content:
    - unwrap FT_insert blocks (inserted new text stays)
    - remove FT_delete blocks (deleted old text disappears)
    """
    ctx = extract_context_content(readable_details)
    if ctx is None:
        return None
    text_after = re.sub(r"<FT_insert>(.*?)</FT_insert>", r"\1", ctx, flags=re.DOTALL)
    text_after = re.sub(r"<FT_delete>.*?</FT_delete>", "", text_after, flags=re.DOTALL)
    return text_after


# ----------------------------
# Diff + patch apply
# ----------------------------

def find_text_diff(text_before: str, text_after: str) -> Tuple[str, str, int]:
    """
    Return (deleted, inserted, pos) using common prefix/suffix.
    pos is the start position of the change in BEFORE text coordinates,
    but it is also the insertion position in AFTER (same pos in aligned sense).
    """
    prefix = 0
    min_len = min(len(text_before), len(text_after))
    while prefix < min_len and text_before[prefix] == text_after[prefix]:
        prefix += 1

    suffix = 0
    while (suffix < min_len - prefix and
           text_before[-(suffix + 1)] == text_after[-(suffix + 1)]):
        suffix += 1

    if suffix > 0:
        deleted = text_before[prefix:len(text_before) - suffix]
        inserted = text_after[prefix:len(text_after) - suffix]
    else:
        deleted = text_before[prefix:]
        inserted = text_after[prefix:]

    return deleted, inserted, prefix


def _all_occurrences(haystack: str, needle: str) -> List[int]:
    if needle == "":
        return []
    out = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        out.append(idx)
        start = idx + 1
    return out


def apply_micro_patch(current: str, deleted: str, inserted: str, pos_hint: int) -> str:
    """
    Apply (deleted -> inserted) to current string.

    Strategy:
    1) If deleted is non-empty: try exact match at pos_hint.
       - If not match, search all occurrences of deleted and choose the one nearest pos_hint.
    2) If deleted is empty (pure insertion): insert at pos_hint (clamped).
    3) If inserted is empty (pure deletion): delete at chosen position.

    Returns new current text.
    """
    # clamp hint
    pos_hint = max(0, min(pos_hint, len(current)))

    if deleted:
        # fast path: exact at hint
        if current.startswith(deleted, pos_hint):
            return current[:pos_hint] + inserted + current[pos_hint + len(deleted):]

        # otherwise choose nearest occurrence
        occ = _all_occurrences(current, deleted)
        if not occ:
            # can't safely apply; fall back: do nothing (or raise)
            # better to be conservative than corrupt text
            return current

        best = min(occ, key=lambda p: abs(p - pos_hint))
        return current[:best] + inserted + current[best + len(deleted):]

    # deleted == "" => insertion
    return current[:pos_hint] + inserted + current[pos_hint:]


def build_marked_after_text(after_text: str, deleted: str, inserted: str, pos: int) -> str:
    """
    Create context_content AFTER operation with ONE accumulated FT marker.
    Convention:
      - Put <FT_delete>deleted</FT_delete> immediately before insertion point
      - Put <FT_insert>inserted</FT_insert> replacing the inserted substring in after_text
    For pure deletion: only FT_delete at pos (no inserted).
    For pure insertion: only FT_insert replacing inserted substring.

    Assumes that if inserted != "", after_text[pos:pos+len(inserted)] equals inserted
    (if not, we still do a best-effort replace at pos).
    """
    pos = max(0, min(pos, len(after_text)))

    out = after_text
    # Replace inserted span in after_text with FT_insert wrapper
    if inserted:
        span = out[pos:pos + len(inserted)]
        # if mismatch, still treat the region as inserted for marking
        out = out[:pos] + f"<FT_insert>{inserted}</FT_insert>" + out[pos + len(inserted):]
        # after wrapping, positions shift; but we insert FT_delete at same logical point
        # which is right before the FT_insert we just placed
        if deleted:
            out = out[:pos] + f"<FT_delete>{deleted}</FT_delete>" + out[pos:]
        return out

    # inserted == "" => pure deletion
    if deleted:
        out = out[:pos] + f"<FT_delete>{deleted}</FT_delete>" + out[pos:]
    return out


# ----------------------------
# Merge logic
# ----------------------------

def can_merge_with_sequence(sequence: List[Dict], next_op: Dict,
                            time_threshold_ms: Optional[int] = None) -> bool:
    if not sequence:
        return False

    if next_op.get("operationType") not in ("edit_text", "cursor_selection"):
        return False

    last = sequence[-1]
    last_path = extract_file_path(last.get("readableDetails", ""))
    next_path = extract_file_path(next_op.get("readableDetails", ""))
    if not last_path or last_path != next_path:
        return False

    if time_threshold_ms is not None:
        last_end = last.get("endTimestamp", last.get("timestamp"))
        dt = next_op["timestamp"] - last_end
        if dt > time_threshold_ms:
            return False

    return True


def edits_share_same_context_window(edits: List[Dict]) -> bool:
    """
    Ensure context_start_line/end_line are identical across edits.
    If any edit lacks context lines, we treat as not safe to merge.
    """
    if not edits:
        return False
    s0, e0 = extract_context_range(edits[0].get("readableDetails", ""))
    if s0 is None or e0 is None:
        return False
    for op in edits[1:]:
        s, e = extract_context_range(op.get("readableDetails", ""))
        if s != s0 or e != e0:
            return False
    return True


def merge_edit_operations_micro(edits: List[Dict]) -> Dict:
    """
    Micro-step apply each edit patch in the SAME context window, then
    produce one merged edit with accumulated FT.

    Uses:
      initial_window = first edit "before"
      for each edit: micro diff from its own before/after, apply to current window
      total diff = diff(initial_window, final_window)
      context_content = final_window marked with ONE FT (total diff)
      readableDetails = last edit readableDetails with context_content replaced
    """
    if len(edits) == 1:
        return edits[0].copy()

    first = edits[0]
    last = edits[-1]

    rd_first = first.get("readableDetails", "")
    rd_last = last.get("readableDetails", "")

    initial = reconstruct_text_before_operation(rd_first)
    if initial is None:
        # conservative fallback
        return last.copy()

    current = initial

    # Apply each edit as patch
    for op in edits:
        rd = op.get("readableDetails", "")
        b = reconstruct_text_before_operation(rd)
        a = reconstruct_text_after_operation(rd)
        if b is None or a is None:
            # skip unsafe patch
            continue
        deleted, inserted, pos = find_text_diff(b, a)
        current = apply_micro_patch(current, deleted, inserted, pos_hint=pos)

    final = current

    # Build ONE accumulated FT from initial->final
    total_deleted, total_inserted, total_pos = find_text_diff(initial, final)
    marked = build_marked_after_text(final, total_deleted, total_inserted, total_pos)

    # Replace context_content in last readableDetails
    new_details = re.sub(
        r"(<context_content>\s*).*?(\s*</context_content>)",
        lambda m: m.group(1) + marked + m.group(2),
        rd_last,
        flags=re.DOTALL
    )

    merged = first.copy()
    merged["timestamp"] = first["timestamp"]
    merged["endTimestamp"] = last.get("endTimestamp", last["timestamp"])
    merged["readableDetails"] = new_details

    # optional metadata
    merged["_merged"] = True
    merged["_original_count"] = len(edits)
    merged["_merged_ids"] = [op["id"] for op in edits]

    return merged


def merge_sequence(sequence: List[Dict]) -> List[Dict]:
    """
    sequence is strict alternation edit/cursor/edit/.../edit
    We merge edits into ONE edit, and we DROP intermediate cursors.
    Trailing cursor (if any) is handled outside.
    """
    edits = [op for op in sequence if op.get("operationType") == "edit_text"]
    if len(edits) <= 1:
        return sequence[:]  # no merge

    # Safety: ensure consistent window
    if not edits_share_same_context_window(edits):
        return sequence[:]  # refuse to merge

    merged_edit = merge_edit_operations_micro(edits)
    return [merged_edit]


def merge_operations(operations: List[Dict],
                     time_threshold_ms: Optional[int] = None,
                     verbose: bool = False) -> List[Dict]:
    """
    Merge strict alternation sequences:
      edit -> cursor -> edit -> cursor -> ... -> edit
    - must start with edit
    - must end with edit
    - any trailing cursor at the end is kept (NOT merged)

    Only merges if:
      - same file
      - passes optional time threshold
      - all edits share same context window
    """
    result: List[Dict] = []
    i = 0
    merge_count = 0

    while i < len(operations):
        op = operations[i]
        if op.get("operationType") != "edit_text":
            result.append(op)
            i += 1
            continue

        sequence = [op]
        j = i + 1
        expected = "cursor_selection"

        while j < len(operations):
            nxt = operations[j]
            if nxt.get("operationType") != expected:
                break
            if not can_merge_with_sequence(sequence, nxt, time_threshold_ms=time_threshold_ms):
                break
            sequence.append(nxt)
            expected = "edit_text" if expected == "cursor_selection" else "cursor_selection"
            j += 1

        # If ends with cursor, keep it as trailing op (not part of merge)
        trailing = []
        if sequence and sequence[-1].get("operationType") == "cursor_selection":
            trailing = [sequence.pop()]

        # Now sequence ends with edit (or single edit)
        merged_seq = merge_sequence(sequence)

        if verbose and len(merged_seq) == 1 and merged_seq[0].get("_merged"):
            merge_count += 1
            ids = merged_seq[0].get("_merged_ids", [])
            print(f"🔄 merged edits: {len(ids)} ops  ids={ids[:3]}...{ids[-3:] if len(ids) >= 3 else ids}")

        result.extend(merged_seq)
        result.extend(trailing)
        i = j

    if verbose:
        print(f"\n📊 merge summary: before={len(operations)} after={len(result)} merged_sequences={merge_count}")

    return result


def process_session_file(input_file: str,
                         output_file: str,
                         time_threshold_ms: Optional[int] = None,
                         verbose: bool = False) -> None:
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    ops = data.get("operations", [])
    original_count = len(ops)

    merged_ops = merge_operations(ops, time_threshold_ms=time_threshold_ms, verbose=verbose)

    data["operations"] = merged_ops
    if "metadata" in data and isinstance(data["metadata"], dict):
        data["metadata"]["total_operations"] = len(merged_ops)
        data["metadata"]["_original_total_operations"] = original_count
        data["metadata"]["_merged"] = True

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ done: {original_count} -> {len(merged_ops)}  (-{original_count - len(merged_ops)})")
    if original_count:
        print(f"   reduction: {(1 - len(merged_ops)/original_count)*100:.1f}%")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python merge_micro.py <input.json> [output.json] [time_threshold_ms]")
        sys.exit(0)

    in_file = sys.argv[1]
    out_file = sys.argv[2] if len(sys.argv) >= 3 else in_file.replace(".json", "_merged.json")
    t_ms = int(sys.argv[3]) if len(sys.argv) >= 4 else None

    process_session_file(in_file, out_file, time_threshold_ms=t_ms, verbose=True)
