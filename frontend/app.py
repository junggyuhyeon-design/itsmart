import html as html_mod
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Generator

import extra_streamlit_components as stx
import httpx
import streamlit as st

logger = logging.getLogger(__name__)

# ── 설정 ─────────────────────────────────────────────────────────
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://codeMind-backend:8000").rstrip("/")
COOKIE_KEY  = "codemind_user_id"
COOKIE_TTL  = timedelta(days=365)
HISTORY_LIMIT   = 50
REQUEST_TIMEOUT = 30.0
STREAM_TIMEOUT  = 300.0
UPLOAD_TIMEOUT  = 300.0
INDEX_TIMEOUT   = 5600.0

st.set_page_config(page_title="IT-Smart Source Analyzer", layout="wide")


# ── 쿠키 ─────────────────────────────────────────────────────────
# 확인 완료
def _get_cookie_manager() -> stx.CookieManager:
    if "_cookie_mgr" not in st.session_state:
        st.session_state["_cookie_mgr"] = stx.CookieManager(key="codemind_cookie_mgr")
    return st.session_state["_cookie_mgr"]

# 확인 완료
def get_or_create_user_id() -> str:
    uid = st.session_state.get("user_id")
    if uid:
        return uid
    uid = (st.context.cookies.get(COOKIE_KEY) or "").strip()
    if uid:
        st.session_state["user_id"] = uid
        return uid
    try:
        uid = (_get_cookie_manager().get(COOKIE_KEY) or "").strip()
        if uid:
            st.session_state["user_id"] = uid
            return uid
    except Exception:
        logger.exception("CookieManager read failed")
    uid = str(uuid.uuid4())
    st.session_state["user_id"] = uid
    try:
        _get_cookie_manager().set(
            COOKIE_KEY, uid, key=COOKIE_KEY,
            expires_at=datetime.now() + COOKIE_TTL, same_site="Lax",
        )
    except Exception:
        logger.exception("Cookie save failed")
    return uid


# ── session_state 초기화 ─────────────────────────────────────────
# 확인 완료
def init_session() -> None:
    defaults: dict[str, Any] = {
        "user_id":           None,
        "messages":          [],
        "history_loaded":    False,
        "selected_project":  None,
        "show_reset_dialog": False,
        "analysis_targets":  [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── API 캐시 ─────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def _cached_projects() -> list[dict]:
    """업로드된 전체 프로젝트 목록 조회"""
    try:
        resp = httpx.get(f"{FASTAPI_URL}/projects", timeout=REQUEST_TIMEOUT)
        if resp.is_success:
            return resp.json().get("projects", [])
    except Exception:
        logger.exception("fetch_projects 예외")
    return []


@st.cache_data(ttl=30, show_spinner=False)
def _cached_system_status() -> dict:
    try:
        resp = httpx.get(f"{FASTAPI_URL}/status", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("fetch_system_status 예외")
        return {}


def fetch_projects(force: bool = False) -> list[dict]:
    """업로드된 전체 프로젝트 목록 조회"""
    if force:
        _cached_projects.clear()
    return _cached_projects()


def fetch_system_status(force: bool = False) -> dict:
    if force:
        _cached_system_status.clear()
    return _cached_system_status()


# ── API 헬퍼 ─────────────────────────────────────────────────────
def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def format_count(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def api_error_message(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
        if detail:
            return str(detail)
    except Exception:
        pass
    return resp.text or f"HTTP {resp.status_code}"


# ── 히스토리 API ─────────────────────────────────────────────────
def fetch_history(user_id: str) -> list[dict]:
    """user_id 로 대화 이력 조회"""
    try:
        resp = httpx.get(
            f"{FASTAPI_URL}/history",
            headers=_headers(user_id),
            params={"limit": HISTORY_LIMIT},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.is_success:
            return list(reversed(resp.json().get("history", [])))
        logger.warning("fetch_history 실패 [%s]: %s", resp.status_code, api_error_message(resp))
    except httpx.TimeoutException:
        st.warning("히스토리 조회 시간이 초과되었습니다.")
    except httpx.ConnectError:
        st.warning("백엔드 서버에 연결할 수 없습니다.")
    except Exception as e:
        logger.exception("fetch_history 예외")
        st.warning(f"히스토리 조회 중 오류: {e}")
    return []

# 확인 완료
def post_history(user_id: str, question: str, answer: str) -> None:
    try:
        resp = httpx.post(
            f"{FASTAPI_URL}/history",
            json={"question": question, "answer": answer},
            headers=_headers(user_id),
            timeout=REQUEST_TIMEOUT,
        )
        if not resp.is_success:
            logger.warning("post_history 실패 [%s]: %s", resp.status_code, api_error_message(resp))
    except httpx.TimeoutException:
        st.warning("히스토리 저장 시간이 초과되었습니다.")
    except Exception as e:
        logger.exception("post_history 예외")
        st.warning(f"히스토리 저장 실패: {e}")

def clear_history_api(user_id: str) -> bool:
    """히스토리를 초기화한다."""
    try:
        resp = httpx.delete(
            f"{FASTAPI_URL}/history",
            headers=_headers(user_id),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.is_success:
            return True
        st.error(f"히스토리 초기화 실패: {api_error_message(resp)}")
    except httpx.TimeoutException:
        st.error("히스토리 초기화 시간이 초과되었습니다.")
    except Exception as e:
        logger.exception("clear_history_api 예외")
        st.error(f"히스토리 초기화 실패: {e}")
    return False


# ── Diagram API ──────────────────────────────────────────────────

_DIAGRAM_TRIGGERS = (
    "관계도", "다이어그램", "mermaid", "머메이드", "diagram",
    "flowchart", "플로우차트", "구조도", "흐름도", "의존관계",
    "그려줘", "그려", "시각화",
)

_ENTITY_HINT_PATTERNS = [
    r"\b([A-Z][A-Za-z0-9]{2,})\s*(?:관련|에\s*관련|의|관계|테이블|구조)",
    r"([A-Z_]{2,})\s*(?:테이블|관련|구조)",          # 대문자 테이블명 (USER, ORDER_ITEM)
    r"(?:관련|에\s*관련된)\s+([A-Za-z가-힣][A-Za-z0-9가-힣_]{1,})",
]

def is_diagram_question(question: str) -> bool:
    """질문 내 Mermaid 관련 단어 유무 확인"""
    q = question.lower()
    return any(k in q for k in _DIAGRAM_TRIGGERS)


def extract_diagram_entity(question: str) -> str | None:
    """
    필터할 엔티티명(테이블명/클래스명)을 추출한다.
    없으면 None → 전체 다이어그램.
    """
    for pat in _ENTITY_HINT_PATTERNS:
        m = re.search(pat, question, re.I)
        if m:
            return m.group(1).strip().upper()
    return None


def fetch_diagram(
    user_id: str,
    project_id: str,
    project_name: str,
    entity_filter: str | None = None,
) -> dict:
    """백엔드 /diagram 엔드포인트 호출 — Mermaid 코드 + 소요시간 반환."""
    params: dict = {
        "project_id"  : project_id,
        "project_name": project_name,
    }
    if entity_filter:
        params["entity_filter"] = entity_filter
    try:
        _t0  = time.perf_counter()
        resp = httpx.get(
            f"{FASTAPI_URL}/diagram",
            params=params,
            headers=_headers(user_id),
            timeout=120.0,
        )
        _elapsed = time.perf_counter() - _t0
        if resp.is_success:
            data = resp.json()
            data["_elapsed"] = _elapsed   # 소요 시간 주입
            return data
        return {"error": api_error_message(resp), "_elapsed": _elapsed}
    except httpx.TimeoutException:
        return {"error": "다이어그램 생성 시간이 초과되었습니다.", "_elapsed": 120.0}
    except httpx.ConnectError:
        return {"error": "백엔드 서버에 연결할 수 없습니다.", "_elapsed": 0.0}
    except Exception as e:
        logger.exception("fetch_diagram 예외")
        return {"error": str(e), "_elapsed": 0.0}


# ── 스트리밍 응답 ─────────────────────────────────────────────────
def get_streaming_response(
    user_id: str,
    question: str,
    project_id: str,
    project_name: str,
) -> Generator[str, None, None]:
    params: dict = {"question": question}
    if project_id:
        params["project_id"]   = project_id
        params["project_name"] = project_name
    try:
        with httpx.Client(timeout=STREAM_TIMEOUT) as client:
            with client.stream(
                "GET", f"{FASTAPI_URL}/ask",
                params=params, headers=_headers(user_id),
            ) as r:
                if not r.is_success:
                    yield f"\n❌ 서버 오류 (HTTP {r.status_code})"
                    return
                for chunk in r.iter_text():
                    if chunk:
                        yield chunk
    except httpx.TimeoutException:
        yield "\n❌ 응답 시간이 초과되었습니다."
    except httpx.ConnectError:
        yield "\n❌ 백엔드 서버에 연결할 수 없습니다."
    except Exception as e:
        logger.exception("get_streaming_response 예외")
        yield f"\n❌ 오류: {e}"


# ── 응답 시간 표시 ────────────────────────────────────────────────
def _elapsed_color(sec: float) -> str:
    """소요 시간에 따라 색상 반환: 빠름(초록) / 보통(주황) / 느림(빨강)"""
    if sec < 5:
        return "#16a34a"   # green-600
    if sec < 30:
        return "#d97706"   # amber-600
    return "#dc2626"       # red-600


def render_elapsed(label: str, sec: float) -> None:
    """
    API 응답 소요 시간을 인라인 뱃지로 표시한다.
    예) ⏱ /index  완료  2.34 초
    """
    color = _elapsed_color(sec)
    st.html(f"""
        <div style="
            display:inline-flex; align-items:center; gap:8px;
            background:#f8fafc; border:1px solid #e2e8f0;
            border-left: 3px solid {color};
            border-radius:6px; padding:5px 12px;
            font-family:monospace; font-size:13px;
            margin:4px 0 8px 0; color:#334155;">
          <span style="color:{color}; font-size:15px;">⏱</span>
          <span style="font-weight:600;">{label}</span>
          <span style="color:#64748b;">완료</span>
          <span style="color:{color}; font-weight:700;">{sec:.2f} 초</span>
        </div>
    """)


# ── Mermaid 렌더링 ────────────────────────────────────────────────
def render_mermaid(mermaid_code: str, height: int = 900) -> None:
    """
    Mermaid 코드를 HTML iframe으로 렌더링.
    """
    import streamlit.components.v1 as components
    if not mermaid_code or not mermaid_code.strip():
        return
    escaped = html_mod.escape(mermaid_code)
    components.html(
        f"""
        <div style="width:100%;overflow:auto;border:1px solid #e5e7eb;
                    border-radius:12px;padding:16px;background:#ffffff;">
            <pre class="mermaid" style="text-align:center;">{escaped}</pre>
        </div>
        <script type="module">
            import mermaid from
                "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
            mermaid.initialize({{
                startOnLoad: true, theme: "default",
                securityLevel: "loose",
                flowchart: {{useMaxWidth: true, htmlLabels: true, curve: "basis"}}
            }});
        </script>
        """,
        height=height,
        scrolling=True,
    )


def extract_mermaid_blocks(text: str) -> list[str]:
    """텍스트에서 mermaid 코드블록을 추출한다."""
    if not text:
        return []
    patterns = [
        r"```mermaid\s*(.*?)```",
        r"```[\r\n]+(graph\s+(?:TD|LR|RL|BT).*?)```",
        r"```[\r\n]+(flowchart\s+(?:TD|LR|RL|BT).*?)```",
    ]
    blocks: list[str] = []
    for pat in patterns:
        for m in re.findall(pat, text, re.I | re.S):
            code = m.strip()
            if code and code not in blocks:
                blocks.append(code)
    if not blocks:
        dm = re.search(r"((?:graph|flowchart)\s+(?:TD|LR|RL|BT)\b.*)", text, re.I | re.S)
        if dm:
            blocks.append(dm.group(1).strip())
    return blocks


def render_message(msg: dict) -> None:
    """
    단일 메시지를 렌더링한다.
    """
    role = msg["role"]
    content = msg["content"]

    mermaid_blocks = []

    with st.chat_message(role):
        if role == "assistant":

            mermaid_blocks = extract_mermaid_blocks(content)

            if mermaid_blocks:
                text_only = re.sub(
                    r"```mermaid.*?```",
                    "",
                    content,
                    flags=re.S
                ).strip()

                if text_only:
                    st.markdown(text_only)
            else:
                st.markdown(content)

        else:
            st.markdown(content)

    if role == "assistant":
        for block in mermaid_blocks:

            render_mermaid(block)


# ── 상태 패널 렌더링 ──────────────────────────────────────────────

_STATUS_LABELS: dict[str, str] = {
    "running":    "🟢 실행 중",
    "available":  "🟢 사용 가능",
    "loaded":     "🟢 로드됨",
    "healthy":    "🟢 정상",
    "stopped":    "🔴 중지됨",
    "missing":    "🔴 없음",
    "not_loaded": "🟡 미로드",
    "error":      "🟠 오류",
    "degraded":   "🟠 일부 문제",
}


def status_label(s: str) -> str:
    return _STATUS_LABELS.get(s, f"⚪ {s}")


def render_status_panel(status: dict) -> None:
    overall = status.get("overall", "degraded")
    st.markdown(f"**전체 상태:** {status_label(overall)}")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("저장된 청크 수", format_count(status.get("chunk_count")))
    with col2:
        rag_ok = status.get("rag_initialized", False)
        st.metric("RAG 서비스", "초기화됨" if rag_ok else "미초기화")

    if not rag_ok and status.get("init_error"):
        st.warning(f"RAG 초기화 오류: {status['init_error']}")

    services = status.get("services", [])
    st.markdown("### 컨테이너 / 서비스")
    if services:
        st.dataframe(
            [{"이름": s.get("name", ""), "컨테이너": s.get("container", "-"),
              "상태": status_label(s.get("status", "")), "설명": s.get("message", ""),
              "URL": s.get("url", "")} for s in services],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("서비스 정보가 없습니다.")

    models = status.get("models", [])
    st.markdown("### 모델")
    if models:
        st.dataframe(
            [{"모델": m.get("name", ""), "유형": m.get("kind", ""),
              "제공자": m.get("provider", "-"), "상태": status_label(m.get("status", "")),
              "설명": m.get("message", "")} for m in models],
            use_container_width=True, hide_index=True,
        )
        ollama_model = next((m for m in models if m.get("kind") == "llm"), None)
        if ollama_model and ollama_model.get("installed_models"):
            with st.expander("Ollama 설치 모델 목록"):
                for name in ollama_model["installed_models"]:
                    st.text(name)
    else:
        st.info("모델 정보가 없습니다.")


# ── 탭 렌더링 ─────────────────────────────────────────────────────

def render_upload_tab(user_id: str) -> None:
    st.subheader("📁 소스 파일 업로드")
    st.info("업로드된 소스 파일은 **모든 사용자가 공통으로 공유**합니다.")

    uploaded_file = st.file_uploader("ZIP 파일 업로드 (최대 1GB)", type=["zip"])

    if st.button("📤 파일 저장 및 소스 수집"):
        if not uploaded_file:
            st.warning("파일을 먼저 업로드해주세요.")
        else:
            with st.spinner("백엔드로 파일 전송 중..."):
                try:
                    resp = httpx.post(
                        f"{FASTAPI_URL}/upload",
                        files=[("files", (
                            uploaded_file.name,
                            uploaded_file.getvalue(),
                            "application/octet-stream",
                        ))],
                        headers=_headers(user_id),
                        timeout=UPLOAD_TIMEOUT,
                    )
                    if resp.is_success:
                        data = resp.json()
                        st.session_state.analysis_targets = data.get("targets", []) # session 에 파일 목록 정보 저장
                        st.success(f"✅ {data.get('count', 0)}개 소스 파일 수집 완료")
                    else:
                        st.error(
                            f"업로드 실패 (HTTP {resp.status_code}): "
                            f"{api_error_message(resp)}"
                        )
                except httpx.TimeoutException:
                    st.error("업로드 시간이 초과되었습니다.")
                except httpx.ConnectError:
                    st.error("백엔드 서버에 연결할 수 없습니다.")
                except Exception as e:
                    logger.exception("업로드 예외")
                    st.error(f"업로드 중 오류: {e}")

    if st.session_state.analysis_targets:
        count = len(st.session_state.analysis_targets)
        st.info(f"수집된 소스 파일: **{count}개** — 아래 버튼으로 인덱싱을 시작하세요.")

        if st.button("🚀 전체 인덱싱 시작 (Qdrant 저장)", type="primary"):
            with st.spinner("인덱싱 중... 파일 크기에 따라 수 분이 걸릴 수 있습니다."):
                try:
                    _t0 = time.perf_counter()
                    resp = httpx.post(
                        f"{FASTAPI_URL}/index",
                        json=st.session_state.analysis_targets,
                        headers=_headers(user_id),
                        timeout=INDEX_TIMEOUT,
                    )
                    _elapsed = time.perf_counter() - _t0
                    if resp.is_success:
                        data = resp.json()
                        total = format_count(data.get("total_chunks") or 0)
                        st.success(f"✅ 인덱싱 완료! 생성된 청크: {total}")
                        render_elapsed("/index", _elapsed)
                        st.session_state.analysis_targets = [] # session 에 저장된 파일구조 초기화
                        if data.get("logs"):
                            with st.expander("인덱싱 로그 보기"):
                                st.text("\n".join(data["logs"]))
                    else:
                        st.error(f"인덱싱 실패: {api_error_message(resp)}")
                except httpx.TimeoutException:
                    st.error("인덱싱 시간이 초과되었습니다.")
                except httpx.ConnectError:
                    st.error("백엔드 서버에 연결할 수 없습니다.")
                except Exception as e:
                    logger.exception("인덱싱 예외")
                    st.error(f"인덱싱 중 오류: {e}")


def render_chat_tab(user_id: str) -> None:
    st.subheader("💬 AI 코드 분석")

    # ── 프로젝트 선택 ──────────────────────────────────────────
    projects     = fetch_projects()
    project_map  = {p["project_name"]: p["project_id"] for p in projects}
    project_names = list(project_map.keys())

    col_proj, col_refresh, col_clear = st.columns([4, 1, 1])
    with col_proj:
        if project_names:
            prev        = st.session_state.selected_project
            default_idx = project_names.index(prev) if prev in project_names else 0
            selected    = st.selectbox(
                "🗂 분석할 프로젝트", project_names, index=default_idx,
                key="project_selectbox",
            )
            st.session_state.selected_project    = selected
            st.session_state.selected_project_id = project_map[selected]
        else:
            st.info("업로드된 프로젝트가 없습니다. 먼저 ZIP 파일을 업로드하고 인덱싱하세요.")
            st.session_state.selected_project    = None
            st.session_state.selected_project_id = None

    with col_refresh:
        if st.button("🔄 새로고침", help="프로젝트 목록 새로고침", use_container_width=True):
            fetch_projects(force=True)
            st.rerun()

    with col_clear:
        if st.button("🗑️ 초기화", key="clear_chat", use_container_width=True):
            with st.spinner("대화 초기화 중..."):
                if clear_history_api(user_id):
                    st.session_state.messages = []
                    st.rerun()

    if st.session_state.selected_project:
        st.caption(f"현재 프로젝트: **{st.session_state.selected_project}**")

    st.divider()

    # ── 누적 대화 렌더링 ───────────────────────────────────────
    for msg in st.session_state.messages:
        render_message(msg)

    # ── 새 질문 입력 ───────────────────────────────────────────
    query = st.chat_input("소스 코드에 대해 궁금한 점을 물어보세요")
    if not query or not query.strip():
        return

    question     = query.strip()
    project_id   = st.session_state.selected_project_id
    project_name = st.session_state.selected_project

    # 사용자 메시지 즉시 표시
    user_msg = {"role": "user", "content": question}
    st.session_state.messages.append(user_msg)
    render_message(user_msg)

    # ── diagram 질문 분기 ──────────────────────────────────────
    if is_diagram_question(question) and project_id:
        entity_filter = extract_diagram_entity(question)   # 특정 엔티티 추출 (없으면 None)

        with st.spinner("소스 파일을 분석해 관계도를 생성하는 중..."):
            result = fetch_diagram(user_id, project_id, project_name, entity_filter)

        _diag_elapsed = result.get("_elapsed", 0.0)

        if "error" in result:
            answer = f"❌ 다이어그램 생성 실패: {result['error']}"
            with st.chat_message("assistant"):
                st.warning(answer)
            render_elapsed("/diagram", _diag_elapsed)

        elif not result.get("mermaid"):
            answer = result.get("message", "다이어그램을 생성할 수 없습니다.")
            with st.chat_message("assistant"):
                st.info(answer)
            render_elapsed("/diagram", _diag_elapsed)

        else:
            mermaid_code = result["mermaid"]
            tables       = result.get("tables", [])
            rel_count    = result.get("relation_count", 0)
            filter_label = f" (필터: {entity_filter})" if entity_filter else ""
            caption      = f"테이블 {len(tables)}개 / 관계 {rel_count}건{filter_label}"
            answer       = f"```mermaid\n{mermaid_code}\n```"

            with st.chat_message("assistant"):
                st.caption(caption)
            render_elapsed("/diagram", _diag_elapsed)
            render_mermaid(mermaid_code)

        assistant_msg = {"role": "assistant", "content": answer}
        st.session_state.messages.append(assistant_msg)
        post_history(user_id, question, answer)
        return

    # ── 일반 질문: /ask 스트리밍 ──────────────────────────────
    collected: list[str] = []
    _ask_t0 = time.perf_counter()

    with st.chat_message("assistant"):
        def _stream_gen():
            for chunk in get_streaming_response(
                user_id=user_id,
                question=question,
                project_id=project_id,
                project_name=project_name,
            ):
                collected.append(chunk)
                yield chunk

        st.write_stream(_stream_gen())
        full_answer = "".join(collected)

    _ask_elapsed = time.perf_counter() - _ask_t0

    if full_answer:
        render_elapsed("/ask", _ask_elapsed)
        assistant_msg = {"role": "assistant", "content": full_answer}
        st.session_state.messages.append(assistant_msg)
        post_history(user_id, question, full_answer)
    else:
        st.session_state.messages.pop()
        st.warning("응답을 받지 못했습니다. 다시 시도해주세요.")


def render_status_tab(user_id: str) -> None:
    st.subheader("⚙️ 시스템 상태")
    st.caption("Docker 컨테이너와 LLM/임베딩 모델의 연결 상태를 확인합니다.")

    refresh_col, _ = st.columns([1, 4])
    with refresh_col:
        refresh_clicked = st.button("🔄 상태 새로고침", type="primary", use_container_width=True)

    status = fetch_system_status(force=refresh_clicked)

    if status:
        render_status_panel(status)
    else:
        st.info("상태 정보가 없습니다. 새로고침 버튼을 눌러주세요.")

    st.divider()
    st.subheader("🗑️ 벡터 DB 초기화")

    if not st.session_state.show_reset_dialog:
        if st.button("⚠️ 모든 벡터 데이터 초기화", type="secondary"):
            st.session_state.show_reset_dialog = True
            st.rerun()
    else:
        st.warning(
            "**주의:** 벡터 DB의 모든 인덱싱 데이터가 삭제됩니다.\n\n"
            "채팅 히스토리는 유지됩니다. 계속하려면 아래에 **RESET** 을 입력하세요."
        )
        confirm_text = st.text_input(
            "초기화 확인", key="reset_confirm_input", placeholder="RESET 입력"
        )
        col1, col2 = st.columns(2)
        with col1:
            if st.button("초기화 실행", type="primary"):
                if confirm_text != "RESET":
                    st.error("RESET 을 정확히 입력해야 합니다.")
                else:
                    with st.spinner("초기화 중..."):
                        try:
                            resp = httpx.delete(
                                f"{FASTAPI_URL}/reset",
                                params={"confirm_text": "RESET"},
                                timeout=REQUEST_TIMEOUT,
                            )
                            if resp.is_success:
                                st.success("✅ 벡터 DB 초기화 완료")
                                st.session_state.show_reset_dialog = False
                                st.session_state.analysis_targets  = []
                                fetch_system_status(force=True)
                                fetch_projects(force=True)
                                st.rerun()
                            else:
                                st.error(f"초기화 실패: {api_error_message(resp)}")
                        except httpx.TimeoutException:
                            st.error("초기화 시간이 초과되었습니다.")
                        except httpx.ConnectError:
                            st.error("백엔드 서버에 연결할 수 없습니다.")
                        except Exception as e:
                            logger.exception("reset 예외")
                            st.error(f"초기화 중 오류: {e}")
        with col2:
            if st.button("취소"):
                st.session_state.show_reset_dialog = False
                st.rerun()


# ── 메인 ─────────────────────────────────────────────────────────

def main() -> None:
    init_session()
    user_id = get_or_create_user_id()

    st.title("🚀 IT-Smart Source Analyzer")
    st.caption(f"🔑 사용자 ID: `{user_id}`")

    # DB 히스토리 최초 1회 복원
    if not st.session_state.history_loaded:
        with st.spinner("이전 대화를 불러오는 중..."):
            rows = fetch_history(user_id)
        st.session_state.messages = [
            msg
            for r in rows
            for msg in (
                {"role": "user",      "content": r["question"]},
                {"role": "assistant", "content": r["answer"]},
            )
        ]
        st.session_state.history_loaded = True

    tabs = st.tabs(["📁 업로드 & 인덱싱", "💬 AI 질문하기", "⚙️ 상태 관리"])
    with tabs[0]:
        render_upload_tab(user_id)
    with tabs[1]:
        render_chat_tab(user_id)
    with tabs[2]:
        render_status_tab(user_id)


if __name__ == "__main__":
    main()
