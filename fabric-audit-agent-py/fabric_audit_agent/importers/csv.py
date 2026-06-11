"""Minimal, dependency-free CSV parser. Faithful port of the Node ``core/importers/csv.js``.

Handles quoted fields, embedded commas / quotes / newlines, CRLF or LF, and a leading
UTF-8 BOM. Values are trimmed. Header row keys each row dict.
"""


def parse_csv(text):
    if not isinstance(text, str) or len(text) == 0:
        return {"headers": [], "rows": []}
    s = text[1:] if text[0] == "﻿" else text   # strip BOM

    records, record, field = [], [], ""
    in_quotes = False
    started = False
    n = len(s)
    i = 0
    while i < n:
        ch = s[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and s[i + 1] == '"':
                    field += '"'
                    i += 1                 # escaped quote
                else:
                    in_quotes = False
            else:
                field += ch
            i += 1
            continue
        if ch == '"':
            in_quotes = True
            started = True
        elif ch == ",":
            record.append(field)
            field = ""
            started = True
        elif ch == "\r":
            pass                            # ignore; LF ends the record
        elif ch == "\n":
            record.append(field)
            field = ""
            records.append(record)
            record = []
            started = False
        else:
            field += ch
            started = True
        i += 1
    if started or len(field) > 0 or len(record) > 0:
        record.append(field)
        records.append(record)

    real = [r for r in records if not (len(r) == 1 and r[0].strip() == "")]
    if not real:
        return {"headers": [], "rows": []}

    headers = [h.strip() for h in real[0]]
    rows = []
    for cells in real[1:]:
        obj = {}
        for idx, h in enumerate(headers):
            obj[h] = (cells[idx] if idx < len(cells) else "").strip()
        rows.append(obj)
    return {"headers": headers, "rows": rows}
