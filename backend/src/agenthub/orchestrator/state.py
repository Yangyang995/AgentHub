"""PipelineAgentState TypedDict ?? LangGraph ????"""

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class PipelineAgentState(TypedDict):
    """@?? Pipeline ?Agent????

    ?? Agent ????????????? Agent ????????????? Agent?
    messages ???? add_messages reducer????????
    """

    # ---- ???? ----
    conversation_id: str
    project_id: str
    user_message: str

    # ---- ?Agent???? ----
    # {capability: execution_id}
    agent_executions: dict[str, str]

    # ---- ????? ----
    # {capability: output_text}
    agent_outputs: dict[str, str]

    # ---- ????? ----
    # {capability: pending/running/succeeded/failed}
    agent_status: dict[str, str]

    # ---- ??????? ----
    conversation_history: list[dict[str, str]]

    # ---- LangGraph ???? ----
    messages: Annotated[list, add_messages]

    # ---- ???? ----
    error: str | None
    pipeline_completed: bool

    # ---- Pipeline ???? ----
    pipeline_steps: list[str]
    current_step_index: int

    # ---- ?????Human-in-the-Loop? ----
    architecture_decision: str  # pending / approved / rejected / modified
    user_feedback: str | None   # ??????
