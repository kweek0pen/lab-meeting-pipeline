"""AI 회의록 출력 스키마 (구조화 출력 + Pydantic 검증).

LLM 구조화 출력의 스키마 제약(선택 필드·유니언 개수 제한)을 피하려고 모든 필드를 '필수'로 두고,
'없음'은 빈 문자열("")로 받는다. 검증 후 minutes.normalize() 가 ""를 null 로 바꿔 저장한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DecisionState = Literal[
    "confirmed",  # 명시적으로 결정·합의됨
    "proposed",  # 가능한 방안으로 제안됨
    "under_consideration",  # 아직 검토 중
    "deferred",  # 의도적으로 보류
    "rejected",  # 명시적으로 배제
    "superseded",  # 이후의 충분히 명확한 결정으로 대체됨
    "unresolved",  # 녹취록만으로 신뢰할 만한 결론을 확정할 수 없음
]
Basis = Literal["explicit", "implied", "uncertain"]  # 명시적 발언 / 맥락상 강하게 함축 / 해석 불확실
RevisionKind = Literal[
    "clarification", "refinement", "conditional_modification", "explicit_reversal", "unresolved_contradiction"
]

DECISION_STATE_KO = {
    "confirmed": "확정", "proposed": "제안", "under_consideration": "검토 중", "deferred": "보류",
    "rejected": "기각", "superseded": "대체됨", "unresolved": "미결",
}
BASIS_KO = {"explicit": "명시적 발언", "implied": "맥락상 함축", "uncertain": "해석 불확실"}
REVISION_KO = {
    "clarification": "명확화", "refinement": "구체화", "conditional_modification": "조건부 수정",
    "explicit_reversal": "명시적 번복", "unresolved_contradiction": "해소되지 않은 모순",
}


class Evidence(BaseModel):
    quote: str = Field(description="녹취록 원문을 그대로 인용. 원문을 고치지 말 것. 요약·의역이면 is_paraphrase=true")
    is_paraphrase: bool
    timestamp: str = Field(description="녹취록 줄 앞의 시간 표기(예: 00:12:30 또는 ≈00:12:30)를 그대로. 없으면 \"\"")


class Alternative(BaseModel):
    option: str
    arguments_for: list[str]
    objections: list[str]
    state: DecisionState
    evidence: list[Evidence]


class Topic(BaseModel):
    topic_id: str = Field(description="T1, T2 …")
    title: str
    context: str = Field(description="왜 이 주제가 논의되었는지, 관련 배경")
    initial_question: str = Field(description="처음 제기된 질문/문제. 없으면 \"\"")
    hypotheses: list[str]
    alternatives: list[Alternative]
    final_position: str = Field(description="확립된 최종 입장. 확립되지 않았으면 \"\"")
    final_state: DecisionState
    remaining_uncertainties: list[str]
    evidence: list[Evidence]


class ReasoningThread(BaseModel):
    topic_id: str
    narrative: str = Field(description="이 주제에서 생각이 어떻게 전개되어 결론(또는 미결)에 이르렀는지 설명. "
                                       "재배열한 경우 실제 발언 순서와 다를 수 있음을 전제로 서술")


class Revision(BaseModel):
    topic_id: str
    earlier_position: str
    later_position: str
    kind: RevisionKind
    supersedes_earlier: bool = Field(description="맥락상 나중 발언이 앞 발언을 대체한다고 볼 근거가 충분할 때만 true")
    explanation: str
    evidence: list[Evidence]


class Decision(BaseModel):
    decision_id: str = Field(description="D1, D2 …")
    topic_id: str
    statement: str
    state: DecisionState
    basis: Basis
    rationale: str = Field(description="왜 이 결론에 이르렀는지")
    evidence: list[Evidence]
    interpretation_notes: str = Field(description="모호함이 남아 있으면 설명. 없으면 \"\"")


class ActionItem(BaseModel):
    task_id: str = Field(description="A1, A2 …")
    task: str
    purpose: str = Field(description="왜 필요한가")
    topic_id: str
    assignees: list[str] = Field(description="녹취록에서 명시적으로 지명된 사람만. 불명확하면 빈 배열")
    assignee_evidence: str = Field(description="담당자를 지명한 발언 원문. 담당자가 없으면 \"\"")
    due_date: str = Field(description="YYYY-MM-DD. 기한이 언급되지 않았거나 해석 불가하면 \"\"")
    due_date_text: str = Field(description="기한을 말한 원래 표현(예: '다음 주 금요일까지'). 없으면 \"\"")
    status: Literal["open", "done"] = Field(description="앞으로 할 일=open, 회의 시점에 이미 완료=done")
    commitment: Literal["confirmed", "tentative"] = Field(
        description="수행하기로 합의/지시됨=confirmed, 해 볼 수도 있다는 수준=tentative")
    basis: Basis
    evidence: list[Evidence]
    context: str = Field(description="이 업무를 이해하는 데 필요한 다른 논의")
    confidence: float = Field(description="0~1 사이 모델 추정치 (검증된 확률 아님)")
    needs_confirmation: bool = Field(description="모순 발언 등으로 '확인 필요'이면 true")
    interpretation_notes: str


class ClarificationItem(BaseModel):
    item: str
    why_unclear: str
    possible_interpretations: list[str]
    topic_id: str
    evidence: list[Evidence]


class ReferenceResolution(BaseModel):
    expression: str = Field(description="'그 방법', '아까 그 결과' 같은 지시 표현")
    timestamp: str
    resolved_to: str = Field(description="가리키는 대상. 확정할 수 없으면 \"\"")
    ambiguous: bool
    notes: str


class Excerpt(BaseModel):
    topic_id: str
    timestamp: str
    quote: str
    why_relevant: str


class QualityCheck(BaseModel):
    check: str
    passed: bool
    notes: str


class MeetingNotes(BaseModel):
    meeting_title: str
    meeting_date: str = Field(description="YYYY-MM-DD")
    overview: str = Field(description="회의 개요: 목적, 다룬 범위 (2~4문장)")
    executive_summary: list[str] = Field(description="핵심 요약 3~7개 항목. 간결하게")
    topics: list[Topic]
    reconstructed_reasoning: list[ReasoningThread]
    decisions: list[Decision] = Field(description="중요한 결론 후보 전부. state 로 확정/제안/보류 등을 구분")
    revisions: list[Revision] = Field(description="방향 전환, 중요한 정정, 모순")
    action_items: list[ActionItem]
    needs_clarification: list[ClarificationItem] = Field(description="의도된 행동 자체가 모호한 항목")
    unresolved_questions: list[str]
    follow_up_questions: list[str] = Field(description="다음에 추가로 확인해야 할 질문")
    reference_resolutions: list[ReferenceResolution]
    key_excerpts: list[Excerpt]
    quality_checks: list[QualityCheck]
