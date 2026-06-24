import logging
import os
import re
import html as html_mod
import uuid
from typing import Any, Generator
from datetime import datetime, timedelta

import httpx
import streamlit as st
import extra_streamlit_components as stx

logger = logging.getLogger(__name__)

# ── 설정 ─────────────────────────────────────────────────────────
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://codeMind-backend:8000").rstrip("/")
COOKIE_KEY  = "codemind_user_id"
COOKIE_TTL  = timedelta(days=365)
HISTORY_LIMIT   = 100
REQUEST_TIMEOUT = 30.0
STREAM_TIMEOUT  = 300.0
UPLOAD_TIMEOUT  = 300.0
INDEX_TIMEOUT   = 1800.0

st.set_page_config(page_title="IT-Smart Source Analyzer", layout="wide")

# ── 쿠키 ─────────────────────────────────────────────────────────

def _get_cookie_manager() -> stx.CookieManager:
    if "_cookie_mgr" not in st.session_state:
        st.session_state["_cookie_mgr"] = stx.CookieManager(key="codemind_cookie_mgr")
    return st.session_state["_cookie_mgr"]


def get_or_create_user_id() -> str:
    # 1. session_state
    uid = st.session_state.get("user_id")
    if uid:
        return uid

    # 2. HTTP Cookie
    uid = (st.context.cookies.get(COOKIE_KEY) or "").strip()

    if uid:
        st.session_state["user_id"] = uid
        return uid

    # 3. CookieManager
    try:
        mgr = _get_cookie_manager()

        uid = (mgr.get(COOKIE_KEY) or "").strip()

        if uid:
            st.session_state["user_id"] = uid
            return uid

    except Exception:
        logger.exception("CookieManager read failed")

    # 4. 신규 UUID 생성
    uid = str(uuid.uuid4())

    st.session_state["user_id"] = uid

    try:
        mgr = _get_cookie_manager()

        mgr.set(
            COOKIE_KEY,
            uid,
            key=COOKIE_KEY,
            expires_at=datetime.now() + COOKIE_TTL,
            same_site="Lax",
        )

    except Exception:
        logger.exception("Cookie save failed")

    return uid

# ── session_state 초기화 ─────────────────────────────────────────

def init_session() -> None:
    """앱 진입 시 1회 실행. 모든 session_state 키를 안전하게 보장한다."""
    defaults: dict[str, Any] = {
        "analysis_targets":  [],
        "db_analysis":       None,
        "messages":          [],     # {"role": "user"|"assistant", "content": str}
        "history_loaded":    False,  # DB 히스토리 최초 1회 로드 완료 여부
        "system_status":     None,   # 상태 탭 캐시
        "show_reset_dialog": False,  # 벡터 DB 초기화 확인 다이얼로그
        "selected_project":  None,   # 현재 선택된 프로젝트 이름
        "projects":          [],     # 프로젝트 목록 캐시
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── API 헬퍼 ────────────────────────────────────────────────────

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
    """해당 사용자의 히스토리를 DB에서 로드. 오래된 순으로 반환."""
    try:
        resp = httpx.get(
            f"{FASTAPI_URL}/history",
            headers=_headers(user_id),
            params={"limit": HISTORY_LIMIT},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.is_success:
            # 백엔드는 최신순(DESC) 반환 → reversed() 로 오래된 순 정렬
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


def post_history(user_id: str, question: str, answer: str) -> None:
    """스트리밍 완료 후 Q&A 쌍을 해당 사용자의 DB에 저장."""
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
    """해당 사용자의 히스토리 전체 삭제. 성공 여부 반환."""
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


# ── 프로젝트 API ─────────────────────────────────────────────────

def fetch_projects() -> list[dict]:
    """백엔드에서 프로젝트 목록 조회."""
    try:
        resp = httpx.get(f"{FASTAPI_URL}/projects", timeout=REQUEST_TIMEOUT)
        if resp.is_success:
            return resp.json().get("projects", [])
    except Exception:
        logger.exception("fetch_projects 예외")
    return []


# ── 스트리밍 응답 ────────────────────────────────────────────────

def get_streaming_response(
    user_id: str,
    question: str,
    extra: str = "",
    project_name: str | None = None,
) -> Generator[str, None, None]:
    params: dict = {"question": question, "extra_context": extra}
    if project_name:
        params["project_name"] = project_name
    try:
        with httpx.Client(timeout=STREAM_TIMEOUT) as client:
            with client.stream(
                "GET",
                f"{FASTAPI_URL}/ask",
                params=params,
                headers=_headers(user_id),
            ) as r:
                if not r.is_success:
                    yield f"\n❌ 서버 오류 (HTTP {r.status_code})"
                    return
                for chunk in r.iter_text():
                    if chunk:
                        yield chunk
    except httpx.TimeoutException:
        yield "\n❌ 응답 시간이 초과되었습니다. 모델이 너무 오래 걸리고 있습니다."
    except httpx.ConnectError:
        yield "\n❌ 백엔드 서버에 연결할 수 없습니다."
    except Exception as e:
        logger.exception("get_streaming_response 예외")
        yield f"\n❌ 오류: {e}"


def fetch_system_status() -> dict:
    resp = httpx.get(f"{FASTAPI_URL}/status", timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ── Mermaid 렌더링 ───────────────────────────────────────────────

def render_mermaid(mermaid_code: str, height: int = 900) -> None:
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


# ── 상태 패널 렌더링 ─────────────────────────────────────────────

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


# ── 탭 렌더링 ────────────────────────────────────────────────────

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
                        st.session_state.analysis_targets = data.get("targets", [])
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
                    resp = httpx.post(
                        f"{FASTAPI_URL}/index",
                        json=st.session_state.analysis_targets,
                        headers=_headers(user_id),
                        timeout=INDEX_TIMEOUT,
                    )
                    if resp.is_success:
                        data = resp.json()
                        total = format_count(data.get("total_chunks") or 0)
                        st.success(f"✅ 인덱싱 완료! 생성된 청크: {total}")
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
    # 앱 시작 or 프로젝트 목록이 비어있으면 갱신
    if not st.session_state.projects:
        st.session_state.projects = fetch_projects()

    projects = st.session_state.projects
    project_names = [p["project_name"] for p in projects]

    col_proj, col_refresh, col_clear = st.columns([4, 1, 1])
    with col_proj:
        if project_names:
            # 이전 선택값이 목록에 없으면 첫 번째로 초기화
            prev = st.session_state.selected_project
            default_idx = project_names.index(prev) if prev in project_names else 0
            selected = st.selectbox(
                "🗂 분석할 프로젝트",
                project_names,
                index=default_idx,
                key="project_selectbox",
            )
            st.session_state.selected_project = selected
        else:
            st.info("업로드된 프로젝트가 없습니다. 먼저 ZIP 파일을 업로드하고 인덱싱하세요.")
            st.session_state.selected_project = None

    with col_refresh:
        if st.button("🔄", help="프로젝트 목록 새로고침", use_container_width=True):
            st.session_state.projects = fetch_projects()
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
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                for block in extract_mermaid_blocks(msg["content"]):
                    render_mermaid(block)

    # ── 새 질문 입력 ───────────────────────────────────────────
    query = st.chat_input("소스 코드에 대해 궁금한 점을 물어보세요")
    if not query or not query.strip():
        return

    question = query.strip()
    project_name = st.session_state.selected_project  # None 이면 전체 프로젝트 대상

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    collected: list[str] = []
    with st.chat_message("assistant"):
        def _gen():
            extra = ""
            if st.session_state.db_analysis:
                extra = (
                    f"\n\n[참고: DB 분석 데이터]\n"
                    f"{st.session_state.db_analysis.get('db_data', '')}"
                )
            for chunk in get_streaming_response(
                user_id, question, extra, project_name=project_name
            ):
                collected.append(chunk)
                yield chunk
        st.write_stream(_gen())

    answer = "".join(collected)
    if answer:
        st.session_state.messages.append({"role": "assistant", "content": answer})
        post_history(user_id, question, answer)
        for block in extract_mermaid_blocks(answer):
            render_mermaid(block)
    else:
        st.session_state.messages.pop()
        st.warning("응답을 받지 못했습니다. 다시 시도해주세요.")


def render_status_tab(user_id: str) -> None:
    st.subheader("⚙️ 시스템 상태")
    st.caption("Docker 컨테이너와 LLM/임베딩 모델의 연결 상태를 확인합니다.")

    refresh_col, _ = st.columns([1, 4])
    with refresh_col:
        refresh_clicked = st.button(
            "🔄 상태 새로고침", type="primary", use_container_width=True
        )

    if refresh_clicked or st.session_state.system_status is None:
        with st.spinner("상태 확인 중..."):
            try:
                st.session_state.system_status = fetch_system_status()
            except httpx.TimeoutException:
                st.session_state.system_status = {}
                st.error("상태 조회 시간이 초과되었습니다.")
            except httpx.ConnectError:
                st.session_state.system_status = {}
                st.error("백엔드 서버에 연결할 수 없습니다.")
            except Exception as e:
                st.session_state.system_status = {}
                st.error(f"상태 조회 실패: {e}")

    if st.session_state.system_status:
        render_status_panel(st.session_state.system_status)
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
                                st.session_state.system_status = None
                                st.session_state.analysis_targets = []
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
        st.session_state.messages = []
        for r in rows:
            st.session_state.messages.append({"role": "user",      "content": r["question"]})
            st.session_state.messages.append({"role": "assistant", "content": r["answer"]})
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
