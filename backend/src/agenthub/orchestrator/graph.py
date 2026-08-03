"""LangGraph Pipeline 图定义 —— @全体 四Agent串行 + 架构审批。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agenthub.orchestrator.state import PipelineAgentState

logger = logging.getLogger(__name__)

# Agent 执行步骤（不含 review）
_AGENT_STEPS = [
    "architecture_design",
    "code_generation",
    "code_review",
    "testing",
]

_STEP_NAMES: dict[str, str] = {
    "architecture_design": "架构设计专家",
    "code_generation": "代码生成专家",
    "code_review": "代码审查专家",
    "testing": "测试专家",
}


def route_after_agent(state: PipelineAgentState) -> str:
    """Agent 节点执行后的路由决策。"""
    if state.get("error"):
        return "finalize"
    current_idx = state.get("current_step_index", 0)
    steps = state.get("pipeline_steps", _AGENT_STEPS)
    if current_idx < len(steps):
        return steps[current_idx]
    return "finalize"


def route_after_review(state: PipelineAgentState) -> str:
    """架构审批后的路由决策。"""
    decision = state.get("architecture_decision", "pending")
    if decision == "rejected":
        return "finalize"
    # approved 或 modified：继续到代码生成
    return "code_generation"


# ---- 闭包引用 ----

_graph_cache: dict[int, Any] = {}
_chat_service_ref = None


def _build_agent_node(capability: str):
    """构建 Agent 执行节点，chat_service 通过闭包注入。"""
    cs = _chat_service_ref

    async def agent_node(state: PipelineAgentState) -> dict[str, Any]:
        if cs is None:
            return {
                "error": "ChatService not available",
                "agent_status": {**state.get("agent_status", {}), capability: "failed"},
            }

        execution_id_str = state.get("agent_executions", {}).get(capability)
        if not execution_id_str:
            return {
                "error": "Missing execution_id for " + capability,
                "agent_status": {**state.get("agent_status", {}), capability: "failed"},
            }
        execution_id = uuid.UUID(execution_id_str)

        agent_status = dict(state.get("agent_status", {}))
        agent_status[capability] = "running"

        try:
            extra_context: list[dict[str, str]] = []
            current_idx = state.get("current_step_index", 0)
            steps = state.get("pipeline_steps", _AGENT_STEPS)
            agent_outputs = state.get("agent_outputs", {})

            for i in range(current_idx):
                prev_cap = steps[i]
                prev_output = agent_outputs.get(prev_cap, "")
                if prev_output:
                    step_name = _STEP_NAMES.get(prev_cap, prev_cap)
                    extra_context.append({
                        "role": "assistant",
                        "content": "[" + step_name + " 的输出]:\n" + prev_output,
                    })

            # 如果有用户反馈（修改意见），注入到上下文
            user_feedback = state.get("user_feedback")
            if user_feedback and capability == "code_generation":
                extra_context.append({
                    "role": "user",
                    "content": (
                        "用户对架构方案的修改意见"
                        "（如与架构设计专家的方案冲突"
                        "，以用户意见为准）:\n" + user_feedback
                    ),
                })

            output = await cs._run_pipeline_step(
                execution_id=execution_id,
                extra_context=extra_context,
            )

            agent_status[capability] = "succeeded"
            new_outputs = dict(agent_outputs)
            new_outputs[capability] = output

            return {
                "agent_outputs": new_outputs,
                "agent_status": agent_status,
                "current_step_index": current_idx + 1,
                "error": None,
            }

        except Exception as exc:
            logger.exception("Pipeline agent node failed: %s", capability)
            agent_status[capability] = "failed"
            step_name = _STEP_NAMES.get(capability, capability)
            return {
                "agent_status": agent_status,
                "error": step_name + " 执行失败: " + str(exc),
                "current_step_index": state.get("current_step_index", 0) + 1,
            }

    return agent_node


async def _review_node(state: PipelineAgentState) -> dict[str, Any]:
    """架构审批节点：暂停 Pipeline 等待用户决策。

    调用 LangGraph interrupt() 暂停执行。
    graph.ainvoke() 会抛出 GraphInterrupt，由 ChatService 捕获。
    用户通过 resume API 传入决策后，Command(resume=...) 恢复执行。
    """
    arch_output = state.get("agent_outputs", {}).get("architecture_design", "")

    # interrupt() 暂停执行，返回值是用户通过 Command(resume=...) 传入的数据
    user_decision = interrupt({
        "type": "architecture_review",
        "architecture_output": arch_output,
    })

    action = user_decision.get("action", "reject") if isinstance(user_decision, dict) else "reject"
    feedback = user_decision.get("feedback", "") if isinstance(user_decision, dict) else ""

    decision_map = {
        "accept": "approved",
        "reject": "rejected",
        "modify": "modified",
    }

    return {
        "architecture_decision": decision_map.get(action, "rejected"),
        "user_feedback": feedback if feedback else None,
    }


async def _finalize_node(state: PipelineAgentState) -> dict[str, Any]:
    cs = _chat_service_ref
    if cs is None:
        return {"pipeline_completed": True}

    conversation_id = uuid.UUID(state["conversation_id"])
    agent_status = state.get("agent_status", {})

    try:
        await cs._finalize_pipeline(conversation_id, agent_status)
    except Exception:
        logger.exception("Pipeline finalize failed")

    return {"pipeline_completed": True}


def build_pipeline_graph(chat_service) -> Any:
    global _chat_service_ref
    _chat_service_ref = chat_service

    cache_key = id(chat_service)
    if cache_key in _graph_cache:
        return _graph_cache[cache_key]

    graph = StateGraph(PipelineAgentState)

    # Agent 执行节点
    graph.add_node("architecture_design", _build_agent_node("architecture_design"))
    graph.add_node("code_generation", _build_agent_node("code_generation"))
    graph.add_node("code_review", _build_agent_node("code_review"))
    graph.add_node("testing", _build_agent_node("testing"))

    # 架构审批节点
    graph.add_node("architecture_review", _review_node)
    graph.add_node("finalize", _finalize_node)

    # 边：START → architecture_design → architecture_review
    graph.add_edge(START, "architecture_design")
    graph.add_edge("architecture_design", "architecture_review")

    # 审批后路由：approve/modify → code_gen，reject → finalize
    graph.add_conditional_edges(
        "architecture_review",
        route_after_review,
        {"code_generation": "code_generation", "finalize": "finalize"},
    )

    # 后续 Agent 的条件边
    for i, step in enumerate(_AGENT_STEPS[1:], start=1):
        next_step = _AGENT_STEPS[i + 1] if i < len(_AGENT_STEPS) - 1 else "finalize"
        graph.add_conditional_edges(
            step,
            route_after_agent,
            {next_step: next_step, "finalize": "finalize"},
        )

    graph.add_edge("finalize", END)

    compiled = graph.compile(checkpointer=InMemorySaver())
    _graph_cache[cache_key] = compiled
    return compiled


async def run_pipeline(
    chat_service,
    execution_ids: list[uuid.UUID],
    conversation_id: uuid.UUID,
    project_id: uuid.UUID,
    user_message: str,
    step_execution_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    if step_execution_map:
        agent_executions = dict(step_execution_map)
    else:
        agent_executions = {}
        for i, step in enumerate(_AGENT_STEPS):
            if i < len(execution_ids):
                agent_executions[step] = str(execution_ids[i])

    initial_state: PipelineAgentState = {
        "conversation_id": str(conversation_id),
        "project_id": str(project_id),
        "user_message": user_message,
        "agent_executions": agent_executions,
        "agent_outputs": {},
        "agent_status": {step: "pending" for step in _AGENT_STEPS if step in agent_executions},
        "conversation_history": [],
        "messages": [],
        "error": None,
        "pipeline_completed": False,
        "pipeline_steps": list(_AGENT_STEPS),
        "current_step_index": 0,
        "architecture_decision": "pending",
        "user_feedback": None,
    }

    graph = build_pipeline_graph(chat_service)

    config = {"configurable": {"thread_id": str(conversation_id)}}
    final_state = await graph.ainvoke(initial_state, config)

    # LangGraph 1.2+: interrupt() ??????? __interrupt__ ??????
    # ? __interrupt__ ??????????????
    if final_state.get("__interrupt__"):
        final_state["_interrupted"] = True

    return final_state
