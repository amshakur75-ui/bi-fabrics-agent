from pathlib import Path

import mlflow
from dotenv import load_dotenv
from mlflow.genai.agent_server import AgentServer

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

# Disable MLflow tracing — we use the [conversation] audit log instead; tracing would require
# a pre-configured experiment path and currently produces INVALID_PARAMETER_VALUE warnings.
mlflow.tracing.disable()

import agent_server.agent  # noqa: E402 — registers @invoke/@stream with mlflow

agent_server_instance = AgentServer("ResponsesAgent", enable_chat_proxy=True)
app = agent_server_instance.app  # noqa: F841
# setup_mlflow_git_based_version_tracking() omitted — SNAPSHOT deploy has no git root


def main():
    agent_server_instance.run(app_import_string="agent_server.start_server:app")