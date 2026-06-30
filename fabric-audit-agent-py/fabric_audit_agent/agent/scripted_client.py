"""Shared offline test-double for the Anthropic messages client.

Used by the eval harness (score_investigations.py) and by tests (test_agent_loop.py,
test_agent_investigator.py). Stdlib-only; no anthropic SDK import required.

Typical usage::

    scripted = [
        Message([Block("tool_use", id="t1", name="investigate_user", input={"user": "x@co"})], "tool_use"),
        Message([Block("text", text="x@co drives 90%.")], "end_turn"),
    ]
    client = ScriptedClient(scripted)
    msg = client.messages.create(model="m", ...)
"""


class Block:
    """Minimal Anthropic content-block stand-in."""
    def __init__(self, type, text=None, id=None, name=None, input=None):
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


class Message:
    """Minimal Anthropic message stand-in."""
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class ScriptedClient:
    """Offline fake client that pops pre-scripted Messages on each ``messages.create()`` call.

    Attributes:
        calls: list of kwargs dicts from each ``create()`` call, for assertion.
    """
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)
