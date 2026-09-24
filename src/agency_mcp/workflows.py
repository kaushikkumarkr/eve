from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from .service import (
    create_campaign_plan,
    extract_voc,
    generate_questions,
    identify_gaps,
    preview_campaign,
    run_visibility_audit,
    validate_campaign_plan,
)


class ResearchState(TypedDict, total=False):
    client_id: str
    segment_ids: list[str]
    questions: list[str]
    test_ids: list[str]
    opportunity_ids: list[str]
    status: str
    error: str


def build_research_graph(session: Session):
    def extract(state: ResearchState) -> ResearchState:
        segments = extract_voc(session, state["client_id"])
        return {**state, "segment_ids": [segment.id for segment in segments], "status": "voc_extracted"}

    def questions(state: ResearchState) -> ResearchState:
        generated = generate_questions(session, state["client_id"])
        return {**state, "questions": generated, "status": "questions_generated"}

    def audit(state: ResearchState) -> ResearchState:
        tests = run_visibility_audit(session, state["client_id"], state["questions"])
        return {**state, "test_ids": [test.id for test in tests], "status": "audit_completed"}

    def gaps(state: ResearchState) -> ResearchState:
        opportunities = identify_gaps(session, state["client_id"])
        return {**state, "opportunity_ids": [item.id for item in opportunities], "status": "gaps_identified"}

    graph = StateGraph(ResearchState)
    graph.add_node("extract_voc", extract)
    graph.add_node("generate_questions", questions)
    graph.add_node("run_audit", audit)
    graph.add_node("identify_gaps", gaps)
    graph.add_edge(START, "extract_voc")
    graph.add_edge("extract_voc", "generate_questions")
    graph.add_edge("generate_questions", "run_audit")
    graph.add_edge("run_audit", "identify_gaps")
    graph.add_edge("identify_gaps", END)
    return graph.compile()


def run_research(session: Session, client_id: str) -> dict[str, Any]:
    graph = build_research_graph(session)
    return graph.invoke({"client_id": client_id, "status": "started"})


class CampaignState(TypedDict, total=False):
    opportunity_id: str
    plan_id: str
    validation: dict[str, Any]
    preview: dict[str, Any]
    status: str
    error: str


def build_campaign_graph(session: Session):
    def draft(state: CampaignState) -> CampaignState:
        plan = create_campaign_plan(session, state["opportunity_id"])
        return {**state, "plan_id": plan.id, "status": "drafted"}

    def validate(state: CampaignState) -> CampaignState:
        result = validate_campaign_plan(session, state["plan_id"])
        return {**state, "validation": result, "status": "validated" if result["valid"] else "rejected"}

    def preview(state: CampaignState) -> CampaignState:
        result = preview_campaign(session, state["plan_id"])
        return {**state, "preview": result, "status": "previewed"}

    graph = StateGraph(CampaignState)
    graph.add_node("draft_campaign", draft)
    graph.add_node("validate_campaign", validate)
    graph.add_node("preview_campaign", preview)
    graph.add_edge(START, "draft_campaign")
    graph.add_edge("draft_campaign", "validate_campaign")
    graph.add_edge("validate_campaign", "preview_campaign")
    graph.add_edge("preview_campaign", END)
    return graph.compile()


def run_campaign(session: Session, opportunity_id: str) -> dict[str, Any]:
    graph = build_campaign_graph(session)
    return graph.invoke({"opportunity_id": opportunity_id, "status": "started"})
