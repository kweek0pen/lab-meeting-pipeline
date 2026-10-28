"""연구실 회의록 자동화 — 로컬 웹 화면 (Streamlit).

실행: start-mac.command / start-windows.bat 더블클릭, 또는  uv run streamlit run app.py
녹음은 브라우저가 아니라 이 컴퓨터의 Python 프로그램이 직접 한다. 브라우저 탭을 닫아도 녹음은 계속되며,
탭을 다시 열면(http://localhost:8501) 상태를 이어서 볼 수 있다.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import threading
from pathlib import Path

import streamlit as st

from labmeeting.logging_setup import setup_logging
from labmeeting.pipeline import Pipeline, Worker, purge_old_recordings
from labmeeting.recorder import Recorder, RecorderError, list_input_devices
from labmeeting.settings import ensure_env_file, load_settings, mask, update_env
from labmeeting.storage import STEP_LABELS, MeetingStore
from labmeeting.titles import fmt_ts, meeting_page_title

st.set_page_config(page_title="연구실 회의록 자동화", page_icon="🎙️", layout="centered")

IN_PROGRESS = {"saving_audio", "transcribing", "generating_minutes", "creating_notion_pages", "linking_pages"}


@st.cache_resource
def runtime() -> dict:
    """프로세스 전체에서 하나만 존재하는 상태 (녹음기, 처리 스레드)."""
    return {"recorder": None, "meeting_id": None, "worker": Worker(), "lock": threading.Lock(), "recovered": False}


ensure_env_file()
settings = load_settings()
settings.ensure_dirs()
log = setup_logging(settings.log_dir, settings.secrets)
store = MeetingStore(settings.recording_dir)
rt = runtime()

# 이전 실행이 녹음 중에 꺼졌던 회의 → '중단됨' 으로 표시 (녹음 조각은 보존되어 있음)
if not rt["recovered"]:
    for m in store.list():
        if m["processing_status"] == "recording" and m["meeting_id"] != rt["meeting_id"]:
            store.update(m["meeting_id"], processing_status="interrupted",
                         error="녹음 중 프로그램이 종료되었습니다. 그때까지 녹음된 내용은 보존되어 있습니다.",
                         recovery_hint="'이어서 처리'를 누르면 보존된 녹음으로 전사부터 진행합니다.")
    rt["recovered"] = True


def pipeline() -> Pipeline:
    return Pipeline(load_settings(), store)


def open_folder(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


def fmt_usd(v) -> str:
    return "계산 불가" if v is None else f"${v:.3f}"


def render_meeting(meta: dict, *, compact: bool = False) -> None:
    mid = meta["meeting_id"]
    status = meta.get("processing_status")
    running = rt["worker"].is_running(mid)
    label = STEP_LABELS.get(status, status)
    detail = f" ({meta['step_detail']})" if meta.get("step_detail") else ""
    if status == "completed":
        st.success(f"{label}")
    elif status in ("failed", "interrupted"):
        st.error(f"{label} — 단계: {STEP_LABELS.get(meta.get('error_step'), meta.get('error_step') or '-')}")
    elif status in IN_PROGRESS and not running:
        st.warning(f"{label}{detail} — 처리가 중간에 멈췄습니다. '이어서 처리'를 누르세요.")
    else:
        st.info(f"{label}{detail}")

    if meta.get("error"):
        st.write(f"**오류:** {meta['error']}")
    if meta.get("recovery_hint"):
        st.caption(f"복구 방법: {meta['recovery_hint']}")

    links = []
    if meta.get("meeting_page_url"):
        links.append(f"[📒 회의록 모음 페이지]({meta['meeting_page_url']})")
    if meta.get("ai_notes_page_url"):
        links.append(f"[🤖 AI 녹취+요약본 페이지]({meta['ai_notes_page_url']})")
    if links:
        st.markdown(" · ".join(links) + ("" if meta.get("relation_linked") else " (Relation 미연결)"))
    for w in meta.get("notion_warnings") or []:
        st.caption(f"⚠️ {w}")

    costs = meta.get("costs") or {}
    if costs:
        parts = []
        if c := costs.get("transcription"):
            parts.append(f"전사 {fmt_usd(c.get('usd'))} (예상, {c.get('billed_minutes')}분)")
        if c := costs.get("summary"):
            parts.append(f"회의록 {fmt_usd(c.get('usd'))} (토큰 사용량 기준, 입력 {c['input_tokens']:,} / 출력 {c['output_tokens']:,})")
        st.caption("API 비용: " + " · ".join(parts) + " — config/pricing.json 가격표 기준이며 실제 청구액과 다를 수 있음")

    if compact:
        return
    cols = st.columns(3)
    can_resume = status not in ("completed", "recording") and not running and mid != rt["meeting_id"]
    if cols[0].button("▶ 이어서 처리", key=f"resume-{mid}", disabled=not can_resume):
        rt["worker"].submit(pipeline(), mid)
        st.rerun()
    if cols[1].button("📂 폴더 열기", key=f"open-{mid}"):
        open_folder(store.meeting_dir(mid))
    if meta.get("minutes_md_path") and Path(meta["minutes_md_path"]).exists():
        cols[2].download_button("⬇ 회의록(.md)", Path(meta["minutes_md_path"]).read_bytes(),
                                file_name=f"{mid}.md", key=f"dl-{mid}")


# ====================================================================== 화면
st.title("🎙️ 연구실 회의록 자동화")
tab_meet, tab_history, tab_settings = st.tabs(["회의", "회의 기록", "설정"])

# ---------------------------------------------------------------------- 회의
with tab_meet:
    missing = settings.missing_all()
    if missing:
        st.warning("아직 설정되지 않은 항목이 있습니다: " + ", ".join(missing) +
                   "\n\n녹음은 지금도 가능하며, '설정' 탭에서 입력한 뒤 '이어서 처리'로 나머지를 진행할 수 있습니다.")

    recorder: Recorder | None = rt["recorder"]
    if recorder is None:
        st.subheader("새 회의")

        def _date_changed():
            new_date = st.session_state["meeting_date"]
            if st.session_state.get("title") in (None, "", st.session_state.get("auto_title")):
                st.session_state["title"] = meeting_page_title(new_date)
            st.session_state["auto_title"] = meeting_page_title(new_date)

        if "auto_title" not in st.session_state:
            st.session_state["auto_title"] = meeting_page_title(dt.date.today())
            st.session_state["title"] = st.session_state["auto_title"]
        meeting_date = st.date_input("회의 날짜", value=dt.date.today(), key="meeting_date", on_change=_date_changed,
                                     help="녹음 날짜와 다르면 바꾸세요. 기한 계산과 Notion 제목에 사용됩니다.")
        title = st.text_input("회의 제목", key="title", help="기본값: YYMMDD(요일) 회의")
        attendees = st.text_input("참석자", placeholder="예: 김교수, 민수, 지현",
                                  help="이름 표기(전사 정확도)에만 쓰이며, AI 가 담당자를 추측하는 근거로 쓰지 않습니다.")
        related = st.text_input("관련 Notion 페이지 URL (선택)")
        devices = list_input_devices()
        options = [None] + [d["index"] for d in devices]
        names = {None: "시스템 기본 마이크"} | {d["index"]: d["name"] + (" (기본)" if d["default"] else "") for d in devices}
        device = st.selectbox("마이크", options, format_func=lambda i: names[i])
        if not devices:
            st.error("마이크를 찾지 못했습니다. 마이크 연결과 운영체제의 마이크 권한을 확인하세요.")

        st.info("🔒 녹음 파일은 이 컴퓨터에 저장됩니다. 회의가 끝나면 녹음은 OpenAI(전사)로, 녹취록은 "
                f"{'Anthropic' if settings.summary_provider == 'anthropic' else 'OpenAI'}(회의록 생성)로 전송되고, "
                "결과는 Notion 에 등록됩니다.")
        consent = st.checkbox("참석자에게 녹음 사실을 알리고 동의를 받았으며, 연구실의 데이터 관리 규칙을 확인했습니다.")
        if st.button("⏺ Start Meeting", type="primary", disabled=not consent or not title.strip(), use_container_width=True):
            with rt["lock"]:
                if rt["recorder"] is None:
                    meta = store.create(meeting_date=meeting_date, title=title.strip(), attendees=attendees.strip(),
                                        related_page_url=related.strip(), device=names[device])
                    rec = Recorder(store.meeting_dir(meta["meeting_id"]), device=device,
                                   samplerate=settings.recording_sample_rate)
                    try:
                        rec.start()
                        rt["recorder"], rt["meeting_id"] = rec, meta["meeting_id"]
                        log.info("회의 시작: %s", meta["meeting_id"])
                    except RecorderError as e:
                        store.update(meta["meeting_id"], processing_status="failed", error=str(e),
                                     error_step="recording", recovery_hint="마이크 설정을 확인한 뒤 새 회의를 시작하세요.")
                        st.error(str(e))
            st.rerun()
    else:
        meta = store.load(rt["meeting_id"])
        st.subheader(meta["title"])
        st.caption(f"회의 날짜 {meta['meeting_date']} · 참석자 {meta.get('attendees') or '-'} · 마이크 {meta.get('device')}")

        @st.fragment(run_every=1)
        def live():
            r: Recorder | None = rt["recorder"]
            if r is None:
                return
            st.markdown(f"<div style='font-size:2.2rem;font-weight:700;color:#d33'>● REC {fmt_ts(r.elapsed)}</div>",
                        unsafe_allow_html=True)
            st.progress(min(1.0, r.level), text="입력 음량")
            if r.silence_suspected():
                st.warning("소리가 전혀 들어오지 않습니다. 운영체제 설정에서 터미널(또는 명령 프롬프트)의 마이크 접근을 "
                           "허용했는지, 올바른 마이크를 골랐는지 확인하세요.")
            if r.error:
                st.error(r.error)

        live()
        st.caption("녹음 중에는 노트북을 덮거나 잠자기로 두지 마세요(자동 잠자기는 막아 둡니다). 브라우저 탭은 닫아도 녹음이 계속됩니다.")
        if st.button("⏹ Stop Meeting", type="primary", use_container_width=True):
            with rt["lock"]:
                rec, mid = rt["recorder"], rt["meeting_id"]
                if rec is not None:
                    rec.stop(finalize=False)
                    rt["recorder"], rt["meeting_id"] = None, None
                    store.update(mid, processing_status="saving_audio")
                    rt["worker"].submit(pipeline(), mid)
                    st.session_state["last_meeting"] = mid
            st.rerun()

    last = st.session_state.get("last_meeting")
    if last and rt["recorder"] is None:
        st.divider()
        st.subheader("최근 처리 결과")

        @st.fragment(run_every=2)
        def progress_view():
            render_meeting(store.load(last), compact=True)

        progress_view()

# ---------------------------------------------------------------------- 기록
with tab_history:
    metas = store.list()
    if not metas:
        st.write("아직 회의 기록이 없습니다.")
    for meta in metas[:30]:
        icon = {"completed": "✅", "failed": "❌", "interrupted": "⚠️", "recording": "🔴"}.get(meta["processing_status"], "⏳")
        with st.expander(f"{icon} {meta['title']} · {meta['meeting_id']}"):
            render_meeting(meta)

    st.divider()
    st.subheader("원본 녹음 보존 정책")
    days = settings.retention_days
    if days <= 0:
        st.caption("현재 설정: 무기한 보존 (RETENTION_DAYS=0). 원본 녹음은 자동으로 삭제되지 않습니다.")
    else:
        st.caption(f"현재 설정: {days}일. 아래 버튼을 눌렀을 때만, 처리 완료된 회의 중 {days}일이 지난 원본 녹음을 삭제합니다.")
        sure = st.checkbox("삭제된 녹음은 복구할 수 없음을 이해했습니다.")
        if st.button("보존 기간 지난 원본 녹음 삭제", disabled=not sure):
            removed = purge_old_recordings(store, days)
            st.success(f"{len(removed)}개 회의의 원본 녹음을 삭제했습니다." if removed else "삭제할 녹음이 없습니다.")

# ---------------------------------------------------------------------- 설정
with tab_settings:
    st.caption("값은 이 프로그램 폴더의 .env 파일에 저장되며 깃헙에 올라가지 않습니다(.gitignore). "
               "API 키 칸은 비워 두면 기존 값이 유지됩니다.")
    with st.form("settings"):
        st.markdown("**API 키**")
        openai_key = st.text_input("OPENAI_API_KEY (전사)", type="password", placeholder=mask(settings.openai_api_key) or "sk-...")
        anthropic_key = st.text_input("ANTHROPIC_API_KEY (회의록 생성)", type="password",
                                      placeholder=mask(settings.anthropic_api_key) or "sk-ant-...")
        notion_key = st.text_input("NOTION_API_KEY (Integration 토큰)", type="password",
                                   placeholder=mask(settings.notion_api_key) or "ntn_...")
        st.markdown("**Notion 데이터베이스** — 링크 전체를 붙여 넣어도 됩니다")
        meeting_db = st.text_input("NOTION_MEETING_DATABASE_ID (회의록 모음 DB)", value=settings.notion_meeting_database_id)
        ai_db = st.text_input("NOTION_AI_NOTES_DATABASE_ID (AI 녹취+요약본 DB)", value=settings.notion_ai_notes_database_id)
        rel = st.text_input("NOTION_MEETING_RELATION_PROPERTY (회의록 모음 DB 의 Relation 속성 이름)",
                            value=settings.notion_meeting_relation_property)
        ai_rel = st.text_input("NOTION_AI_NOTES_RELATION_PROPERTY (선택: 단방향 Relation 일 때만)",
                               value=settings.notion_ai_notes_relation_property)
        with st.expander("선택 항목"):
            mdate = st.text_input("NOTION_MEETING_DATE_PROPERTY (회의록 모음 DB 의 날짜 속성 이름)",
                                  value=settings.notion_meeting_date_property)
            adate = st.text_input("NOTION_AI_NOTES_DATE_PROPERTY", value=settings.notion_ai_notes_date_property)
            prel = st.text_input("NOTION_MEETING_PROJECT_RELATION_PROPERTY (관련 프로젝트 Relation 속성 이름)",
                                 value=settings.notion_meeting_project_relation_property)
            glossary = st.text_area("GLOSSARY (연구실 용어·고유명사, 쉼표로 구분)", value=settings.glossary)
            provider = st.selectbox("SUMMARY_PROVIDER", ["anthropic", "openai"],
                                    index=0 if settings.summary_provider == "anthropic" else 1)
            summary_model = st.text_input("SUMMARY_MODEL (비우면 기본 모델)", value="")
            stt_model = st.text_input("TRANSCRIPTION_MODEL", value=settings.transcription_model)
            retention = st.number_input("RETENTION_DAYS (0 = 무기한 보존)", min_value=0, value=settings.retention_days)
        if st.form_submit_button("저장", type="primary"):
            update_env({
                "OPENAI_API_KEY": openai_key, "ANTHROPIC_API_KEY": anthropic_key, "NOTION_API_KEY": notion_key,
                "NOTION_MEETING_DATABASE_ID": meeting_db, "NOTION_AI_NOTES_DATABASE_ID": ai_db,
                "NOTION_MEETING_RELATION_PROPERTY": rel, "NOTION_AI_NOTES_RELATION_PROPERTY": ai_rel,
                "NOTION_MEETING_DATE_PROPERTY": mdate, "NOTION_AI_NOTES_DATE_PROPERTY": adate,
                "NOTION_MEETING_PROJECT_RELATION_PROPERTY": prel, "GLOSSARY": glossary.replace("\n", ", "),
                "SUMMARY_PROVIDER": provider, "SUMMARY_MODEL": summary_model, "TRANSCRIPTION_MODEL": stt_model,
                "RETENTION_DAYS": str(int(retention)),
            })
            st.success("저장했습니다.")
            st.rerun()

    st.markdown(f"현재 회의록 생성: **{settings.summary_provider} / {settings.summary_model}** · "
                f"전사: **{settings.transcription_model}**")
    if st.button("🔌 연결 테스트"):
        s = load_settings()
        with st.spinner("확인 중…"):
            if s.openai_api_key:
                try:
                    from openai import OpenAI

                    OpenAI(api_key=s.openai_api_key, max_retries=1).models.retrieve(s.transcription_model)
                    st.success(f"OpenAI 연결 OK ({s.transcription_model})")
                except Exception as e:
                    st.error(f"OpenAI 연결 실패: {type(e).__name__}: {getattr(e, 'message', e)}")
            else:
                st.warning("OPENAI_API_KEY 없음")
            if s.summary_provider == "anthropic":
                if s.anthropic_api_key:
                    try:
                        import anthropic

                        anthropic.Anthropic(api_key=s.anthropic_api_key, max_retries=1).models.retrieve(s.summary_model)
                        st.success(f"Anthropic 연결 OK ({s.summary_model})")
                    except Exception as e:
                        st.error(f"Anthropic 연결 실패: {type(e).__name__}: {getattr(e, 'message', e)}")
                else:
                    st.warning("ANTHROPIC_API_KEY 없음")
            if not s.missing_for_notion():
                try:
                    from labmeeting.notion_client import NotionClient
                    from labmeeting.notion_sync import resolve_targets

                    t = resolve_targets(NotionClient(s.notion_api_key), s)
                    st.success("Notion 연결 OK — 두 데이터베이스 접근 가능, Relation 확인됨 "
                               f"({'양방향' if t.relation_dual else '단방향'})")
                except Exception as e:
                    st.error(f"Notion 확인 실패: {e}")
            else:
                st.warning("Notion 설정 미완료: " + ", ".join(s.missing_for_notion()))
