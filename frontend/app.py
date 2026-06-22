"""
IT-Smart Source Analyzer — Streamlit 프론트엔드

사용자 식별 전략:
  - streamlit-cookies-manager 로 실제 HTTP 쿠키에 UUID v4 저장
  - 브라우저를 닫아도 쿠키 만료 전까지 동일 UUID 유지
  - 모든 API 요청에 X-User-Id 헤더 포함
  - 업로드 파일은 전체 공유 / 채팅 히스토리는 사용자별 분리
  - 페이지 재진입 시 DB 히스토리를 자동 복원해 대화 누적 표시
"""
import os
import re
import html as html_mod
import uuid

import httpx
import streamlit as st

st.set_page_config(page_title="IT-Smart Source Analyzer", layout="wide")

from streamlit_cookies_manager import EncryptedCookieManager

FASTAPI_URL = os.getenv("FASTAPI_URL", "http://codeMind-backend:8000")
COOKIE_PASSWORD = os.getenv("COOKIE_PASSWORD", "codemind-secret-key-change-in-prod")
COOKIE_KEY = "codemind_user_id"

# ── 쿠키 매니저 초기화 ───────────────────────────────────────────
# prefix로 이 앱의 쿠키를 다른 앱과 구분
cookies = EncryptedCookieManager(prefix="codemind_", password=COOKIE_PASSWORD)

if not cookies.ready():
    # 쿠키 로드 전 대기 (컴포넌트 초기화 필요)
    st.stop()


# ── UUID 획득 / 신규 생성 ────────────────────────────────────────

def get_or_create_user_id() -> str:
    uid = cookies.get(COOKIE_KEY, "").strip()
    if not uid:
        uid = str(uuid.uuid4())
        cookies[COOKIE_KEY] = uid
        cookies.save()
    return uid


# ── session_state 초기화 (단일 정의) ────────────────────────────

def init_session():
    defaults = {
        "analysis_targets": [],
        "db_analysis":      None,
        "messages":         [],     # {"role": "user"|"assistant", "content": str}
        "history_loaded":   False,  # DB 히스토리 최초 1회 로드 여부
        "system_status":    None,   # 상태 관리 탭 캐시
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── API 헬퍼 ────────────────────────────────────────────────────

def _headers(user_id: str) -> dict:
    return {"X-User-Id": user_id}


def format_count(value) -> str:
    return f"{(value or 0):,}"


def api_error_message(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
        if detail:
            return str(detail)
    except Exception:
        pass
    return resp.text or f"HTTP {resp.status_code}"


def fetch_history(user_id: str) -> list:
    """DB에서 사용자 히스토리 로드 후 오래된 순으로 반환."""
    try:
        resp = httpx.get(
            f"{FASTAPI_URL}/history",
            headers=_headers(user_id),
            params={"limit": 100},
            timeout=30.0,
        )
        if resp.is_success:
            return list(reversed(resp.json().get("history", [])))
    except Exception:
        pass
    return []


def post_history(user_id: str, question: str, answer: str):
    """스트리밍 완료 후 Q&A를 DB에 저장."""
    try:
        httpx.post(
            f"{FASTAPI_URL}/history",
            json={"question": question, "answer": answer},
            headers=_headers(user_id),
            timeout=30.0,
        )
    except Exception as e:
        st.warning(f"히스토리 저장 실패: {e}")


def clear_history_api(user_id: str):
    try:
        httpx.delete(
            f"{FASTAPI_URL}/history",
            headers=_headers(user_id),
            timeout=30.0,
        )
    except Exception as e:
        st.error(f"히스토리 초기화 실패: {e}")


def get_streaming_response(user_id: str, question: str, extra: str = ""):
    try:
        with httpx.Client(timeout=300.0) as client:
            with client.stream(
                "GET",
                f"{FASTAPI_URL}/ask",
                params={"question": question, "extra_context": extra},
                headers=_headers(user_id),
            ) as r:
                for chunk in r.iter_text():
                    yield chunk
    except Exception as e:
        yield f"\n❌ 백엔드 연결 실패: {str(e)}"


def fetch_system_status() -> dict:
    resp = httpx.get(f"{FASTAPI_URL}/status", timeout=30.0)
    resp.raise_for_status()
    return resp.json()


# ── Mermaid 렌더링 ───────────────────────────────────────────────

def render_mermaid(mermaid_code: str, height: int = 900):
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
            mermaid.initialize({{startOnLoad:true,theme:"default",securityLevel:"loose",
                flowchart:{{useMaxWidth:true,htmlLabels:true,curve:"basis"}}}});
        </script>
        """,
        height=height,
        scrolling=True,
    )


def extract_mermaid_blocks(text: str) -> list:
    if not text:
        return []
    patterns = [
        r"```mermaid\s*(.*?)```",
        r"```[\r\n]+(graph\s+(?:TD|LR|RL|BT).*?)```",
        r"```[\r\n]+(flowchart\s+(?:TD|LR|RL|BT).*?)```",
    ]
    blocks = []
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

def status_label(status: str) -> str:
    labels = {
        "running": "🟢 실행 중", "available": "🟢 사용 가능",
        "loaded":  "🟢 로드됨",  "healthy":   "🟢 정상",
        "stopped": "🔴 중지됨",  "missing":   "🔴 없음",
        "not_loaded": "🟡 미로드", "error":   "🟠 오류",
        "degraded": "🟠 일부 문제",
    }
    return labels.get(status, f"⚪ {status}")


def render_status_panel(status: dict):
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
    st.markdown("### 컨테이너 / 서비스")
    service_rows = []
    for svc in status.get("services", []):
        service_rows.append({
            "이름": svc.get("name", ""),
            "컨테이너": svc.get("container", "-"),
            "상태": status_label(svc.get("status", "")),
            "설명": svc.get("message", ""),
            "URL": svc.get("url", ""),
        })
    if service_rows:
        st.dataframe(service_rows, use_container_width=True, hide_index=True)
    else:
        st.info("서비스 정보가 없습니다.")

    st.markdown("### 모델")
    model_rows = []
    for model in status.get("models", []):
        row = {
            "모델": model.get("name", ""),
            "유형": model.get("kind", ""),
            "제공자": model.get("provider", "-"),
            "상태": status_label(model.get("status", "")),
            "설명": model.get("message", ""),
        }
        model_rows.append(row)
    if model_rows:
        st.dataframe(model_rows, use_container_width=True, hide_index=True)

    ollama_model = next((m for m in status.get("models", []) if m.get("kind") == "llm"), None)
    if ollama_model and ollama_model.get("installed_models"):
        with st.expander("Ollama에 설치된 모델 목록"):
            for name in ollama_model["installed_models"]:
                st.text(name)
    # services = status.get("services", [])
    # if services:
    #     st.markdown("### 컨테이너 / 서비스")
    #     st.dataframe(
    #         [{"이름": s.get("name"), "컨테이너": s.get("container", "-"),
    #           "상태": status_label(s.get("status", "")), "설명": s.get("message", ""),
    #           "URL": s.get("url", "")} for s in services],
    #         use_container_width=True, hide_index=True,
    #     )

    # models = status.get("models", [])
    # if models:
    #     st.markdown("### 모델")
    #     st.dataframe(
    #         [{"모델": m.get("name"), "유형": m.get("kind"), "제공자": m.get("provider", "-"),
    #           "상태": status_label(m.get("status", "")), "설명": m.get("message", "")}
    #          for m in models],
    #         use_container_width=True, hide_index=True,
    #     )
    #     ollama_model = next((m for m in models if m.get("kind") == "llm"), None)
    #     if ollama_model and ollama_model.get("installed_models"):
    #         with st.expander("Ollama에 설치된 모델 목록"):
    #             for name in ollama_model["installed_models"]:
    #                 st.text(name)


# ── 메인 ─────────────────────────────────────────────────────────

def main():
    init_session()  # 모든 session_state 키를 여기서 한 번에 초기화

    # 쿠키에서 UUID 획득 (없으면 신규 생성 + 저장)
    user_id = get_or_create_user_id()

    st.title("🚀 IT-Smart Source Analyzer")
    st.caption(f"🔑 사용자 ID: `{user_id}`")

    # DB 히스토리 최초 1회 복원 (브라우저 재접속 시 대화 유지)
    if not st.session_state.history_loaded:
        rows = fetch_history(user_id)
        st.session_state.messages = []
        for r in rows:
            st.session_state.messages.append({"role": "user",      "content": r["question"]})
            st.session_state.messages.append({"role": "assistant", "content": r["answer"]})
        st.session_state.history_loaded = True

    tabs = st.tabs(["📁 업로드 & 인덱싱", "💬 AI 질문하기", "⚙️ 상태 관리"])

    # ── Tab 0: 업로드 & 인덱싱 ──────────────────────────────────
    with tabs[0]:
        st.subheader("📁 소스 파일 업로드 (전체 공유)")
        st.info("업로드된 소스는 모든 사용자가 공통으로 사용합니다.")
        files = st.file_uploader("ZIP 파일 업로드", type=["zip"])

        if st.button("파일 저장 및 분석 실행"):
            if not files:
                st.warning("파일을 먼저 업로드해주세요.")
            else:
                with st.spinner("백엔드로 파일 전송 중..."):
                    try:
                        resp = httpx.post(
                            f"{FASTAPI_URL}/upload",
                            files=[("files", (files.name, files.getvalue(), "application/octet-stream"))],
                            headers=_headers(user_id),
                            timeout=300.0,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        st.session_state.analysis_targets = data.get("targets", [])
                        st.success(f"✅ {data.get('count', 0)}개 파일 수집 완료")
                    except httpx.HTTPStatusError as e:
                        st.error(f"ERROR {e.response.status_code}: {e.response.json().get('detail', str(e))}")
                    except Exception as e:
                        st.error(f"백엔드 통신 에러: {str(e)}")

        if st.session_state.analysis_targets:
            st.info(f"현재 수집된 파일: {len(st.session_state.analysis_targets)}개")
            if st.button("🚀 전체 인덱싱 시작 (Qdrant 저장)", type="primary"):
                with st.spinner("백엔드에서 인덱싱 중..."):
                    try:
                        resp = httpx.post(
                            f"{FASTAPI_URL}/index",
                            json=st.session_state.analysis_targets,
                            headers=_headers(user_id),
                            timeout=1800.0,
                        )
                        if resp.is_success:
                            data = resp.json()
                            st.success(f"✅ 인덱싱 완료! 생성된 청크: {format_count(data.get('total_chunks') or 0)}")
                            if data.get("logs"):
                                with st.expander("인덱싱 로그"):
                                    st.text("\n".join(data["logs"]))
                        else:
                            st.error(f"인덱싱 실패: {api_error_message(resp)}")
                    except Exception as e:
                        st.error(f"백엔드 통신 에러: {e}")

    # ── Tab 1: AI 질문 (대화형 누적) ────────────────────────────
    with tabs[1]:
        st.subheader("💬 AI 코드 분석 (RAG)")

        if st.button("🗑️ 대화 초기화", key="clear_chat"):
            clear_history_api(user_id)
            st.session_state.messages = []
            st.rerun()

        # 누적 대화 렌더링
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg["role"] == "assistant":
                    for block in extract_mermaid_blocks(msg["content"]):
                        render_mermaid(block)

        # 새 질문 입력
        query = st.chat_input("소스 코드에 대해 궁금한 점을 물어보세요")
        if query:
            st.session_state.messages.append({"role": "user", "content": query})
            with st.chat_message("user"):
                st.markdown(query)

            with st.chat_message("assistant"):
                collected: list[str] = []

                def _gen():
                    extra = ""
                    if st.session_state.db_analysis:
                        extra = f"\n\n[참고: DB 분석 데이터]\n{st.session_state.db_analysis.get('db_data')}"
                    for chunk in get_streaming_response(user_id, query, extra):
                        collected.append(chunk)
                        yield chunk

                full_text = st.write_stream(_gen())

            answer = "".join(collected) if collected else (full_text or "")
            st.session_state.messages.append({"role": "assistant", "content": answer})

            if answer:
                post_history(user_id, query, answer)

            for block in extract_mermaid_blocks(answer):
                render_mermaid(block)

            st.rerun()

    # ── Tab 2: 상태 관리 ─────────────────────────────────────────
    with tabs[2]:
        st.subheader("⚙️ 시스템 상태")
        st.caption("Docker 컨테이너와 LLM/임베딩 모델의 연결 상태를 확인합니다.")

        refresh_col, _ = st.columns([1, 4])
        with refresh_col:
            refresh_clicked = st.button("🔄 상태 새로고침", type="primary", use_container_width=True)

        # system_status는 init_session()에서 None으로 초기화되어 있으므로 안전
        if refresh_clicked or st.session_state.system_status is None:
            with st.spinner("상태 확인 중..."):
                try:
                    st.session_state.system_status = fetch_system_status()
                except Exception as e:
                    st.session_state.system_status = {}
                    st.error(f"백엔드 연결 실패: {e}")

        if st.session_state.system_status:
            render_status_panel(st.session_state.system_status)
        else:
            st.info("상태 정보가 없습니다. 새로고침 버튼을 눌러주세요.")

        st.divider()

        if st.button("⚠️ 모든 벡터 데이터 초기화", type="secondary"):
            try:
                httpx.delete(f"{FASTAPI_URL}/reset", headers=_headers(user_id))
                st.success("벡터 데이터베이스가 초기화되었습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"초기화 실패: {e}")


if __name__ == "__main__":
    main()
