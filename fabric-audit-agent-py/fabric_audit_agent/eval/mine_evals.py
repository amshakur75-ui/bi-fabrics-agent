"""Eval-flywheel growth loop (mining half, Phase 5.4b): turn the ``[conversation]`` audit log
(5.4a) into candidate golden agent-eval SKELETONS for ``eval/agent_cases.json``. Mirrors the 3-A
``query/mine.py`` pattern (parse -> shape_key -> rank_candidates -> to_*_entries).

No I/O here (stdlib ``json``/``re``/``hashlib``/``collections`` only) -- the CLI (later task) owns
file reads and stdout. Preview-only by construction: ``to_eval_skeletons`` NEVER fabricates a
``script``; the placeholder is a plain string, so ``score_agent_case``'s ``_client_from_script``
(which iterates ``script`` expecting dicts) raises if a skeleton is ever scored unedited -- see
``docs/superpowers/specs/2026-07-09-eval-miner-design.md`` for the full honesty rationale.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict

# The `[conversation] ` audit line is `print(f"[conversation] {json.dumps(payload, ...)}")`
# (agent_server/agent.py:_conversation_audit_log). A logger prefix may precede the marker, so we
# match on the substring, not line-start -- same convention as query/mine.py's `[adhoc-kql] `.
_MARKER = "[conversation] "

# --------------------------------------------------------------------------------------------
# shape_key
# --------------------------------------------------------------------------------------------

# Time-of-day tokens ("3pm", "9:05 am") -- normalized BEFORE bare numbers so "3pm" doesn't get
# partially digit-mangled by the numeric rule first. Case-insensitive is moot here since shape_key
# lowercases first, but the pattern is written to only match already-lowercased am/pm.
_TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b")

# Any remaining bare number (dates, thresholds, counts, ids, ...).
_NUMBER_RE = re.compile(r"\d+")

# Double-quoted content is unambiguous -- blank it whole.
_DQUOTE_RE = re.compile(r'"[^"]*"')

# Single-quoted content is ambiguous with English contractions/possessives ("didn't", "alice's").
# Only treat a '...' span as a quoted value when neither the opening nor the closing quote is
# glued to a word character -- i.e. it looks like a delimiter, not an apostrophe inside a word.
# This is what keeps negation words ("didn't", "wasn't", "can't") intact for the negation tests.
_SQUOTE_RE = re.compile(r"(?<!\w)'[^']*'(?!\w)")

# Trailing punctuation/whitespace run at the very end of the (already whitespace-collapsed) text.
_TRAILING_PUNCT_RE = re.compile(r"[\s?.!,;:]+$")

_WHITESPACE_RE = re.compile(r"\s+")


def shape_key(question) -> str:
    """Canonical grouping key for a mined user question. Deterministic, pure.

    In order: (1) lowercase; (2) collapse whitespace; (3) strip trailing punctuation; (4)
    time-of-day tokens ("3pm"/"9:05 am") -> "<TIME>" (so "spike at 3pm" and "...9am" merge); (5)
    remaining bare numbers -> "<N>"; (6) quoted-string contents -> "<S>". Negation words
    (not/no/n't) are never touched, so "did it spike" and "did it NOT spike" stay distinct shapes.
    """
    s = str(question).lower()
    s = _WHITESPACE_RE.sub(" ", s).strip()
    s = _TRAILING_PUNCT_RE.sub("", s)
    s = _TIME_RE.sub("<TIME>", s)
    s = _NUMBER_RE.sub("<N>", s)
    s = _DQUOTE_RE.sub('"<S>"', s)
    s = _SQUOTE_RE.sub("'<S>'", s)
    return s


# --------------------------------------------------------------------------------------------
# parse_conversation_lines
# --------------------------------------------------------------------------------------------

def parse_conversation_lines(lines) -> list[dict]:
    """For each line containing the substring "[conversation] " (a logger prefix may precede
    it), json.loads the text after the marker. Skips non-marker lines and malformed/non-dict JSON
    (including the sibling failure line `[conversation] log failed: <Type>`, which is not valid
    JSON) -- NEVER raises. Keeps only records with tag == "conversation". ``lines`` may be a list
    of strings or any iterable of strings.
    """
    out = []
    for line in lines:
        if not isinstance(line, str):
            continue
        idx = line.find(_MARKER)
        if idx == -1:
            continue
        payload = line[idx + len(_MARKER):]
        try:
            rec = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("tag") != "conversation":
            continue
        out.append(rec)
    return out


# --------------------------------------------------------------------------------------------
# rank_candidates
# --------------------------------------------------------------------------------------------

def _last_user_message(case) -> str | None:
    """The last {"role":"user", ...} message's content in an agent_cases.json-style case's
    ``messages`` list (5.4a only ever logs the last user message, so this is the fair comparison
    point for dedup). None if there is no user message."""
    last = None
    for m in case.get("messages") or ():
        if isinstance(m, dict) and m.get("role") == "user":
            last = m.get("content")
    return last


def rank_candidates(records, existing_cases, *, min_count=2, top_n=20) -> list[dict]:
    """Group *records* (parsed ``[conversation]`` records) by ``shape_key(question)``. Drop any
    shape already covered by an *existing_cases* golden case (``shape_key`` applied to that
    case's last user message). Keep groups with ``count >= min_count``.

    - representative ``question`` = the most-frequent EXACT question text in the group; ties
      broken lexicographically (ascending) for determinism.
    - ``expectTool`` = the most-common single tool name across the group's ``toolsCalled`` lists
      (ties broken lexicographically); ``None`` if no member of the group ever called a tool.
    - ``expectAbstain`` = the majority ``abstainedHint`` across the group (strict majority; a tie
      resolves to ``False`` -- never left blank, since the scorer reads a missing/None
      ``expectAbstain`` as ``False`` anyway, and a blank would hide that this is a guess).
    - ``observedTools`` / ``abstainHintCounts`` surface the raw vote spread so a human reviewing
      the skeleton can see ``expectTool``/``expectAbstain`` are unverified hints, not ground truth.

    Survivors are sorted deterministically by ``(hitCount DESC, shapeKey ASC)`` and the top
    *top_n* are returned as ``{"question", "expectTool", "expectAbstain", "hitCount",
    "observedTools", "abstainHintCounts"}`` dicts (shapeKey itself is NOT part of the returned
    shape -- it is only the internal/sort grouping key). Pure, no I/O; tolerates malformed input.
    """
    if not records:
        return []

    existing_shapes = set()
    for case in existing_cases or ():
        if not isinstance(case, dict):
            continue
        last_user = _last_user_message(case)
        if last_user is None:
            continue
        existing_shapes.add(shape_key(last_user))

    # shapeKey -> list of records, in input order.
    groups = defaultdict(list)
    for rec in records:
        if not isinstance(rec, dict):
            continue
        question = rec.get("question")
        if question is None:
            continue
        groups[shape_key(question)].append(rec)

    candidates = []  # list of (shapeKey, candidate_dict) so we can sort by shapeKey without
    # exposing it in the returned schema.
    for shape, members in groups.items():
        if shape in existing_shapes:
            continue

        hit_count = len(members)
        if hit_count < min_count:
            continue

        question_freq = Counter(m.get("question") for m in members)
        max_q_freq = max(question_freq.values())
        representative = min(q for q, c in question_freq.items() if c == max_q_freq)

        tool_counter = Counter()
        for m in members:
            for t in (m.get("toolsCalled") or ()):
                tool_counter[t] += 1
        if tool_counter:
            max_tool_freq = max(tool_counter.values())
            expect_tool = min(t for t, c in tool_counter.items() if c == max_tool_freq)
        else:
            expect_tool = None

        true_count = sum(1 for m in members if bool(m.get("abstainedHint")))
        false_count = hit_count - true_count
        expect_abstain = true_count > false_count

        candidates.append((shape, {
            "question": representative,
            "expectTool": expect_tool,
            "expectAbstain": expect_abstain,
            "hitCount": hit_count,
            "observedTools": dict(tool_counter),
            "abstainHintCounts": {"true": true_count, "false": false_count},
        }))

    candidates.sort(key=lambda pair: (-pair[1]["hitCount"], pair[0]))
    return [c for _shape, c in candidates[:top_n]]


# --------------------------------------------------------------------------------------------
# to_eval_skeletons
# --------------------------------------------------------------------------------------------

# NEVER a real script -- see module docstring. `_client_from_script` (score_investigations.py)
# does `for b in script: ... b["type"]`; iterating a str yields one-char strings, and indexing a
# str with a string key ("type") raises TypeError -- fail-loud if scored unedited.
SCRIPT_PLACEHOLDER = "REPLACE-ME: author replay fixtures (this string ERRORS if run)"

_NAME_UNSAFE_RE = re.compile(r"[^a-z0-9]+")
_NAME_MAX_BASE_LEN = 40


def _kebab(text: str) -> str:
    """Fold *text* to a lowercase, hyphenated [a-z0-9-] slug; empty/unsafe input becomes 'case'."""
    s = _NAME_UNSAFE_RE.sub("-", str(text).lower()).strip("-")
    return (s[:_NAME_MAX_BASE_LEN].rstrip("-")) or "case"


def to_eval_skeletons(ranked, existing_cases) -> list[dict]:
    """Project each ``rank_candidates`` output dict into a golden-case SKELETON:
    ``{"name","messages","expectTool","expectAbstain","script","_minedFrom"}``.

    - ``name`` = ``f"mined-{kebab(question)}-{h}"`` where ``h`` is
      ``hashlib.sha1(question.encode()).hexdigest()[:6]``. Uniqueness is enforced against
      *existing_cases*' names and against names already emitted earlier in this batch; on
      collision, ``h`` is lengthened one hex character at a time (per the 3-A
      ``to_library_entries`` precedent) until unique.
    - ``script`` is always ``SCRIPT_PLACEHOLDER`` -- never fabricated.
    - ``_minedFrom`` is non-schema provenance (hitCount/observedTools/abstainHintCounts) for the
      human to review and then strip before committing the case.

    Pure, no I/O.
    """
    if not ranked:
        return []

    used_names = set()
    for case in existing_cases or ():
        if isinstance(case, dict) and case.get("name") is not None:
            used_names.add(case["name"])

    skeletons = []
    for cand in ranked:
        question = cand.get("question")
        base = _kebab(question)
        digest = hashlib.sha1(str(question).encode("utf-8")).hexdigest()

        length = 6
        name = f"mined-{base}-{digest[:length]}"
        while name in used_names and length < len(digest):
            length += 1
            name = f"mined-{base}-{digest[:length]}"
        used_names.add(name)

        skeletons.append({
            "name": name,
            "messages": [{"role": "user", "content": question}],
            "expectTool": cand.get("expectTool"),
            "expectAbstain": cand.get("expectAbstain"),
            "script": SCRIPT_PLACEHOLDER,
            "_minedFrom": {
                "hitCount": cand.get("hitCount"),
                "observedTools": cand.get("observedTools"),
                "abstainHintCounts": cand.get("abstainHintCounts"),
            },
        })

    return skeletons
