"""RegexEngine — interprets a RegexTable to parse a signal text.

Pure function, no I/O. Reuses _norm_num from parsers.numbers.
"""

from __future__ import annotations

import re

from tg_listener.models import ParsedSignalFields
from tg_listener.parsers.numbers import _norm_num
from tg_listener.parsers.regex_table import RegexSlot, RegexTable

# Mapping from flag name to re flag constant.
_FLAG_MAP: dict[str, re.RegexFlag] = {
    "IGNORECASE": re.IGNORECASE,
    "DOTALL": re.DOTALL,
    "MULTILINE": re.MULTILINE,
    "UNICODE": re.UNICODE,
}


def _compile(slot: RegexSlot) -> re.Pattern[str]:
    flags = re.RegexFlag(0)
    for name in slot.flags:
        flags |= _FLAG_MAP[name]
    return re.compile(slot.pattern, flags)


def parse(table: RegexTable, text: str) -> ParsedSignalFields | None:
    """Parse *text* using the data-driven *table*.

    Returns ParsedSignalFields on success, None if any required field is missing
    or Pydantic validation fails.
    """
    # Step 1 — pre-clean.
    clean = re.sub(table.pre_clean, " ", text, flags=re.UNICODE) if table.pre_clean else text

    # Step 2 — side.
    side_re = _compile(table.side)
    side_m = side_re.search(clean)
    if not side_m:
        return None
    side_keyword = side_m.group(table.side.group).lower()
    side = table.side_map.get(side_keyword)
    if side is None:
        return None

    # Step 3 — symbol.
    symbol: str | None = None
    if table.symbol_from_side_group is not None:
        try:
            raw = side_m.group(table.symbol_from_side_group)
            if raw:
                symbol = raw.upper()
        except IndexError:
            pass
    elif table.symbol is not None:
        sym_re = _compile(table.symbol)
        skip = frozenset(s.upper() for s in table.skip_symbols)
        for m in sym_re.finditer(text.upper()):
            candidate = m.group(table.symbol.group).upper()
            if candidate not in skip:
                symbol = candidate
                break
    if symbol is None:
        return None

    # Step 4 — entry (zone first, then single).
    entry_value: float | tuple[float, float]
    if table.entry_zone is not None:
        zone_re = _compile(table.entry_zone)
        zone_m = zone_re.search(clean)
        if zone_m:
            try:
                entry_value = (_norm_num(zone_m.group(1)), _norm_num(zone_m.group(2)))
            except (ValueError, IndexError):
                return None
        else:
            entry_re = _compile(table.entry)
            entry_m = entry_re.search(clean)
            if not entry_m:
                return None
            try:
                entry_value = _norm_num(entry_m.group(table.entry.group))
            except ValueError:
                return None
    else:
        entry_re = _compile(table.entry)
        entry_m = entry_re.search(clean)
        if not entry_m:
            return None
        try:
            entry_value = _norm_num(entry_m.group(table.entry.group))
        except ValueError:
            return None

    # Step 5 — SL.
    sl_re = _compile(table.sl)
    sl_m = sl_re.search(clean)
    if not sl_m:
        return None
    try:
        sl = _norm_num(sl_m.group(table.sl.group))
    except ValueError:
        return None

    # Step 6 — TPs.
    tp_re = _compile(table.tp)
    tp_comma_re = re.compile(table.tp_comma_list) if table.tp_comma_list else None
    tp_split_re = re.compile(table.tp_split) if table.tp_split else None

    tp: list[float] = []
    for tp_m in tp_re.finditer(clean):
        raw_tail = tp_m.group(table.tp.group)
        if tp_comma_re:
            raw_tail = tp_comma_re.sub(" ", raw_tail)
        if tp_split_re:
            parts = tp_split_re.split(raw_tail)
        else:
            parts = [raw_tail]
        for part in parts:
            part = part.strip()
            if not part:
                continue
            try:
                tp.append(_norm_num(part))
            except ValueError:
                pass
    if not tp:
        return None

    # Step 7 — leverage (optional).
    leverage: int | None = None
    if table.leverage is not None:
        lev_re = _compile(table.leverage)
        lev_m = lev_re.search(clean)
        if lev_m:
            try:
                leverage = int(lev_m.group(table.leverage.group))
            except (ValueError, IndexError):
                pass

    # Step 8 — build model.
    try:
        return ParsedSignalFields(
            symbol=symbol,
            side=side,
            entry=entry_value,
            sl=sl,
            tp=tp,
            leverage=leverage,
        )
    except Exception:
        return None
