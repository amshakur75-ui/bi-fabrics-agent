"""Databricks deploy entry — the authored MLflow ``ResponsesAgent`` wrapping the offline-tested
investigation core (Phase-2 Part B / B1+B2).

DEPLOY-ONLY. This module imports ``mlflow`` / ``databricks`` / ``anthropic`` and is loaded ONLY in
the serving / Databricks-App environment via ``mlflow.pyfunc.log_model(python_model="app/agent.py")``
(models-from-code). It is NOT imported by the package or the test suite, so the stdlib-only core and
``python -m pytest -q`` are unaffected.

The agent logic itself is the offline-tested ``fabric_audit_agent.agent.investigator.investigate`` —
this file only (a) builds an OBO client at query time and (b) maps to/from the MLflow Responses schema.

VERIFY AT DEPLOY TIME (these APIs evolve — confirm against current docs; see docs/PHASE2-DEPLOY.md):
  - the MLflow ResponsesAgent request/response surface,
  - how this workspace exposes the Databricks-hosted Claude endpoint (Anthropic Messages protocol vs
    OpenAI chat-completions — see _build_client below and PHASE2-DEPLOY.md §B1).
"""
import os

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse
from mlflow.entities import SpanType
from mlflow.models import set_model

from fabric_audit_agent.agent.investigator import investigate

mlflow.anthropic.autolog()  # capture the raw tool-loop calls as traces

_MODEL = os.environ.get("DATABRICKS_CLAUDE_ENDPOINT", "databricks-claude-opus-4-7")


def _build_client():
    """OBO Anthropic-Messages client pointed at the in-tenant Databricks-hosted Claude endpoint.
    Built per-request (identity known at query time; never in __init__) per the OBO docs.

    VERIFY: confirm this endpoint speaks the Anthropic Messages protocol. If your workspace exposes
    only the OpenAI chat-completions protocol for this endpoint, replace this with the
    OpenAI->Anthropic shape adapter in docs/PHASE2-DEPLOY.md §B1-alt (the loop only needs an object
    with ``.messages.create(...)`` returning content blocks + ``stop_reason``)."""
    from databricks.sdk import WorkspaceClient
    from databricks_ai_bridge import ModelServingUserCredentials
    import anthropic

    w = WorkspaceClient(credentials_strategy=ModelServingUserCredentials())
    # NOTE: extracting a bearer for a 3rd-party SDK under OBO is the bit to verify. The robust
    # alternative (PHASE2-DEPLOY.md §B1-alt) calls w.serving_endpoints.query(...) directly and
    # adapts the response, so the SDK owns the OBO auth.
    return anthropic.Anthropic(base_url=f"{w.config.host}/serving-endpoints/{_MODEL}",
                               api_key=w.config.token)


def _messages_from_request(request):
    """Flatten a ResponsesAgentRequest's input items to [{role, content}] for the core loop."""
    msgs = []
    for item in getattr(request, "input", None) or []:
        role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None)
        content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if role:
            msgs.append({"role": role, "content": content})
    return msgs or [{"role": "user", "content": ""}]


class CapacityInvestigatorAgent(ResponsesAgent):
    @mlflow.trace(span_type=SpanType.AGENT)
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        result = investigate(_messages_from_request(request), _build_client(), model=_MODEL)
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=result["output_text"], id="msg_1")],
            custom_outputs={"trajectory": result["trajectory"],
                            "toolResults": result["toolResults"],
                            "stoppedReason": result["stoppedReason"]},
        )


set_model(CapacityInvestigatorAgent())
