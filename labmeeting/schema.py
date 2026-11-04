"""AI 회의록 출력 스키마 (Pydantic 검증).

핵심은 outline: 사람이 직접 쓴 회의록처럼 안건별 짧은 개조식 요약.
decisions / action_items 는 근거 발언과 함께 따로 받아, 담당자·기한 날조를 코드가 검사하고
'근거' 토글에만 보여 준다. '없음'은 빈 문자열("")로 받고 minutes.normalize() 가 null 로 바꾼다.
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

DECISION_STATE_KO = {
    "confirmed": "확정", "proposed": "제안", "under_consideration": "검토 중", "deferred": "보류",
    "rejected": "기각", "superseded": "대체됨", "unresolved": "미결",
}
BASIS_KO = {"explicit": "명시적 발언", "implied": "맥락상 함축", "uncertain": "해석 불확실"}


class OutlineItem(BaseModel):
    text: str = Field(description="짧은 개조식 한 줄")
    todo: bool = Field(description="해야 할 일이면 true (체크박스로 표시)")
    children: list[OutlineItem] = Field(description="하위 항목. 최대 3단계까지")


class OutlineSection(BaseModel):
    title: str = Field(description="안건/주제 이름 (짧게)")
    items: list[OutlineItem]


class Evidence(BaseModel):
    quote: str = Field(description="녹취록 원문을 그대로 인용. 요약·의역이면 is_paraphrase=true")
    is_paraphrase: bool
    timestamp: str = Field(description="녹취록 줄 앞의 시간 표기 그대로. 없으면 \"\"")


class Decision(BaseModel):
    decision_id: str
    statement: str
    state: DecisionState
    basis: Basis
    evidence: list[Evidence]
    note: str = Field(description="모호함·조건이 있으면 짧게. 없으면 \"\"")


class ActionItem(BaseModel):
    task_id: str
    task: str = Field(description="짧게")
    assignees: list[str] = Field(description="녹취록에서 명시적으로 지명된 사람만. 불명확하면 빈 배열")
    assignee_evidence: str = Field(description="담당자를 지명한 발언 원문. 담당자가 없으면 \"\"")
    due_date: str = Field(description="YYYY-MM-DD. 기한이 언급되지 않았거나 해석 불가하면 \"\"")
    due_date_text: str = Field(description="기한을 말한 원래 표현. 없으면 \"\"")
    status: Literal["open", "done"]
    commitment: Literal["confirmed", "tentative"]
    basis: Basis
    evidence: list[Evidence]
    confidence: float = Field(description="0~1 사이 모델 추정치 (검증된 확률 아님)")
    needs_confirmation: bool
    note: str = Field(description="모순·모호함이 있으면 짧게. 없으면 \"\"")


class MeetingNotes(BaseModel):
    meeting_title: str
    meeting_date: str = Field(description="YYYY-MM-DD")
    outline: list[OutlineSection] = Field(description="핵심 회의록. 안건별 짧은 개조식")
    decisions: list[Decision] = Field(description="중요한 결론만 (확정·제안·보류 등 상태 구분)")
    action_items: list[ActionItem] = Field(description="회의에서 나온 할 일 전부")
    needs_clarification: list[str] = Field(description="다음 회의에서 확인할 것. 최대 5개, 한 줄씩")
