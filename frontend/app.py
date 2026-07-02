import os
import re
import time
<<<<<<< HEAD
import uuid
from datetime import datetime, timedelta
from typing import Any, Generator
=======
from datetime import datetime
from typing import Any
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9

import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

BACKEND_URL = os.getenv("FASTAPI_URL", "http://codeMind-backend:8000")

<<<<<<< HEAD
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
def _get_cookie_manager() -> stx.CookieManager:
    if "_cookie_mgr" not in st.session_state:
        st.session_state["_cookie_mgr"] = stx.CookieManager(key="codemind_cookie_mgr")
    return st.session_state["_cookie_mgr"]


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
def init_session() -> None:
    defaults: dict[str, Any] = {
        "user_id":           None,
        "messages":          [],
        "history_loaded":    False,
        "selected_project":  None,
        "show_reset_dialog": False,
        "analysis_targets":  [],
=======
st.set_page_config(
    page_title="IT-Smart CodeMind",
    page_icon="🧠",
    layout="wide",
)


def init_session_state():
    defaults = {
        "user_id": "local-user",
        "projects": [],
        "projects_error": None,
        "system_status": None,
        "system_status_error": None,
        "index_jobs": [],
        "index_job_error": None,
        "history_items": [],
        "history_error": None,
        "latest_project_name": None,
        "chat_project_select": "전체",
        "active_job_id": None,
        "active_job_detail": None,
        "uploading": False,
        "indexing": False,
        "last_uploaded_targets": [],
        "last_upload_result": None,
        "last_uploaded_file_sig": "",
        "show_reset_confirm": False,
        "project_histories": {},
        "uploader_nonce": 0,
        "pending_upload": None,
        "pending_upload_sig": "",
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


<<<<<<< HEAD
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
=======
init_session_state()


def get_headers() -> dict[str, str]:
    return {"X-User-Id": st.session_state.user_id}


def api_get(path: str, params: dict | None = None, timeout: int = 30, stream: bool = False):
    return requests.get(
        f"{BACKEND_URL}{path}",
        headers=get_headers(),
        params=params,
        timeout=timeout,
        stream=stream,
    )


def api_post(path: str, json_data: Any = None, files=None, timeout: int = 120):
    return requests.post(
        f"{BACKEND_URL}{path}",
        headers=get_headers(),
        json=json_data,
        files=files,
        timeout=timeout,
    )


def api_delete(path: str, params: dict | None = None, timeout: int = 30):
    return requests.delete(
        f"{BACKEND_URL}{path}",
        headers=get_headers(),
        params=params,
        timeout=timeout,
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
    )


def extract_mermaid_blocks(text: str) -> list[str]:
    """텍스트에서 mermaid 코드블록을 추출한다."""
    if not text:
        return []
    return [
        m.strip()
        for m in re.findall(r"```mermaid\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    ]


<<<<<<< HEAD
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
=======
def strip_mermaid_blocks(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"```mermaid\s*.*?```", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9


def render_mermaid(mermaid_code: str, height: int = 650):
    safe_code = (
        mermaid_code.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    html = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8"/>
        <script type="module">
          import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
          mermaid.initialize({{
            startOnLoad: true,
            securityLevel: 'loose',
            theme: 'default',
            flowchart: {{ useMaxWidth: true, htmlLabels: true }},
            er: {{ useMaxWidth: true }},
            sequence: {{ useMaxWidth: true }}
          }});
        </script>
        <style>
          html, body {{ margin:0; padding:0; background:#fff; }}
          body {{ padding:8px; }}
          .wrap {{
            width:100%;
            overflow:auto;
            border:1px solid #e5e7eb;
            border-radius:8px;
            padding:12px;
            box-sizing:border-box;
            background:#fff;
          }}
          .mermaid {{ min-width:900px; }}
        </style>
      </head>
      <body>
        <div class="wrap">
          <pre class="mermaid">{safe_code}</pre>
        </div>
      </body>
    </html>
    """
    components.html(html, height=height, scrolling=True)


def render_answer(content: str):
    text_part = strip_mermaid_blocks(content)
    mermaid_blocks = extract_mermaid_blocks(content)

    if text_part:
        st.markdown(text_part)

    for idx, block in enumerate(mermaid_blocks, start=1):
        st.caption(f"Diagram {idx}")
        render_mermaid(block)


def parse_created_at_to_ts(value: str | None) -> float:
    if not value:
        return time.time()

    patterns = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S.%f",
    ]

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]

<<<<<<< HEAD
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
=======
    for fmt in patterns:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            pass

    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return time.time()
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9


def normalize_project_name(name: str | None) -> str:
    return (name or "").strip() or "이름없음"

<<<<<<< HEAD
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
=======

def current_project_name() -> str:
    return st.session_state.get("chat_project_select", "전체")


def dedupe_projects(projects: list[dict]) -> list[dict]:
    by_project_id: dict[str, dict] = {}
    for p in projects:
        pid = (p.get("project_id") or "").strip()
        if not pid:
            continue
        existing = by_project_id.get(pid)
        if not existing:
            by_project_id[pid] = p
            continue

        old_uploaded = existing.get("uploaded_at") or ""
        new_uploaded = p.get("uploaded_at") or ""
        if new_uploaded >= old_uploaded:
            by_project_id[pid] = p

    unique_by_name: dict[str, dict] = {}
    for p in sorted(by_project_id.values(), key=lambda x: x.get("uploaded_at") or "", reverse=True):
        pname = normalize_project_name(p.get("project_name"))
        if pname not in unique_by_name:
            unique_by_name[pname] = p

    return list(unique_by_name.values())


def fetch_system_status(force: bool = False):
    if st.session_state.system_status is not None and not force:
        return st.session_state.system_status
    try:
        r = api_get("/status", timeout=15)
        r.raise_for_status()
        st.session_state.system_status = r.json()
        st.session_state.system_status_error = None
    except Exception as e:
        st.session_state.system_status = None
        st.session_state.system_status_error = str(e)
    return st.session_state.system_status


def fetch_projects(force: bool = False):
    if st.session_state.projects and not force:
        return st.session_state.projects
    try:
        r = api_get("/projects", timeout=20)
        r.raise_for_status()
        data = r.json()
        raw_projects = data.get("projects", [])
        st.session_state.projects = dedupe_projects(raw_projects)
        st.session_state.projects_error = None

        valid_names = {"전체"} | {normalize_project_name(p.get("project_name")) for p in st.session_state.projects}
        if st.session_state.chat_project_select not in valid_names:
            st.session_state.chat_project_select = "전체"

    except Exception as e:
        st.session_state.projects = []
        st.session_state.projects_error = str(e)
    return st.session_state.projects


def fetch_index_jobs(force: bool = False):
    if st.session_state.index_jobs and not force:
        return st.session_state.index_jobs
    try:
        r = api_get("/index/jobs", params={"limit": 50}, timeout=20)
        r.raise_for_status()
        data = r.json()
        st.session_state.index_jobs = data.get("jobs", [])
        st.session_state.index_job_error = None
    except Exception as e:
        st.session_state.index_jobs = []
        st.session_state.index_job_error = str(e)
    return st.session_state.index_jobs


def fetch_index_job_detail(job_id: str):
    try:
        r = api_get(f"/index/jobs/{job_id}", timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_history(force: bool = False):
    if st.session_state.history_items and not force:
        return st.session_state.history_items
    try:
        r = api_get("/history", params={"limit": 300}, timeout=20)
        r.raise_for_status()
        data = r.json()
        st.session_state.history_items = data.get("history", [])
        st.session_state.history_error = None
    except Exception as e:
        st.session_state.history_items = []
        st.session_state.history_error = str(e)
    return st.session_state.history_items


def rebuild_project_histories_from_server():
    histories = st.session_state.get("history_items") or []
    project_histories: dict[str, list[dict]] = {}

    ordered = list(reversed(histories))

    for item in ordered:
        q = (item.get("question") or "").strip()
        a = (item.get("answer") or "").strip()
        created_at = item.get("created_at")
        ts = parse_created_at_to_ts(created_at)

        project = "전체"
        q_stripped = q

        if q.startswith("[") and "]" in q:
            try:
                project = q[1:q.index("]")].strip() or "전체"
                q_stripped = q[q.index("]") + 1 :].strip()
            except Exception:
                project = "전체"
                q_stripped = q

        if project not in project_histories:
            project_histories[project] = []

        if q_stripped:
            project_histories[project].append(
                {"role": "user", "content": q_stripped, "ts": ts}
            )

        if a:
            project_histories[project].append(
                {"role": "assistant", "content": a, "ts": ts}
            )
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9

    st.session_state.project_histories = project_histories


def calc_job_progress(job: dict) -> int:
    total = int(job.get("total_targets") or 0)
    processed = int(job.get("processed_targets") or 0)
    status = (job.get("status") or "").lower()

<<<<<<< HEAD
    # ── 누적 대화 렌더링 ───────────────────────────────────────
    for msg in st.session_state.messages:
        render_message(msg)
=======
    if status == "completed":
        return 100
    if total <= 0:
        return 0
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9

    pct = int((processed / total) * 100)
    return max(0, min(99 if status in {"queued", "running"} and processed < total else 100, pct))


def build_project_job_map(projects: list[dict], jobs: list[dict]) -> dict[str, dict]:
    result = {}
    for project in projects:
        pname = normalize_project_name(project.get("project_name"))
        matched = [j for j in jobs if normalize_project_name(j.get("project_name")) == pname]
        if not matched:
            continue
        matched.sort(
            key=lambda x: (
                x.get("updated_at") or "",
                x.get("created_at") or "",
            ),
            reverse=True,
        )
        result[pname] = matched[0]
    return result


def get_project_status_label(job: dict | None) -> str:
    if not job:
        return "준비됨"

    status = (job.get("status") or "").lower()
    progress = calc_job_progress(job)

    if status == "queued":
        return f"대기 중 {progress}%"
    if status == "running":
        return f"인덱싱 중 {progress}%"
    if status == "completed":
        return "인덱싱 완료"
    if status == "failed":
        return "인덱싱 실패"
    return status or "준비됨"


def project_selectable(job: dict | None) -> bool:
    if not job:
        return True
    status = (job.get("status") or "").lower()
    progress = calc_job_progress(job)
    if status in {"queued", "running"} and progress < 100:
        return False
    return status == "completed" or progress >= 100


def get_visible_chat_messages() -> list[dict]:
    selected = current_project_name()
    if selected == "전체":
        return []
    return st.session_state.project_histories.get(selected, [])


def reset_local_state_after_reset():
    st.session_state.projects = []
    st.session_state.projects_error = None
    st.session_state.index_jobs = []
    st.session_state.index_job_error = None
    st.session_state.history_items = []
    st.session_state.history_error = None
    st.session_state.latest_project_name = None
    st.session_state.chat_project_select = "전체"
    st.session_state.active_job_id = None
    st.session_state.active_job_detail = None
    st.session_state.uploading = False
    st.session_state.indexing = False
    st.session_state.last_uploaded_targets = []
    st.session_state.last_upload_result = None
    st.session_state.last_uploaded_file_sig = ""
    st.session_state.show_reset_confirm = False
    st.session_state.project_histories = {}
    st.session_state.pending_upload = None
    st.session_state.pending_upload_sig = ""
    st.session_state.uploader_nonce += 1


def render_system_status():
    status = fetch_system_status()
    st.sidebar.subheader("시스템 상태")

    if not status:
        err = st.session_state.system_status_error or "상태 조회 실패"
        st.sidebar.error(err)
        return

<<<<<<< HEAD
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
=======
    overall = status.get("overall", "unknown")
    rag_initialized = status.get("rag_initialized", False)

    if overall == "healthy":
        st.sidebar.success("정상")
    elif overall == "degraded":
        st.sidebar.warning("부분 장애")
    else:
        st.sidebar.error("비정상")

    st.sidebar.caption(f"RAG 초기화: {'완료' if rag_initialized else '미완료'}")

    for svc in status.get("services", []):
        name = svc.get("name", "-")
        svc_status = svc.get("status", "unknown")
        message = svc.get("message", "")
        icon = "🟢" if svc_status == "running" else ("🟡" if svc_status == "degraded" else "🔴")
        st.sidebar.caption(f"{icon} {name} - {svc_status}")
        if message:
            st.sidebar.caption(f"↳ {message}")


def render_sidebar_projects():
    st.sidebar.subheader("프로젝트 공간")

    projects = fetch_projects(force=True)
    jobs = fetch_index_jobs(force=True)
    project_job_map = build_project_job_map(projects, jobs)

    if not projects:
        st.sidebar.info("프로젝트가 없습니다.")
        return

    if st.sidebar.button("전체 보기", key="all_projects_btn", use_container_width=True):
        st.session_state.chat_project_select = "전체"
        st.rerun()

    current = current_project_name()

    for project in projects:
        pname = normalize_project_name(project.get("project_name"))
        pid = (project.get("project_id") or "").strip() or pname
        job = project_job_map.get(pname)

        disabled = not project_selectable(job)
        status_label = get_project_status_label(job)
        progress = calc_job_progress(job) if job else None
        selected = current == pname

        label = f"📁 {pname}"
        if selected:
            label += " ✅"

        if st.sidebar.button(
                label,
                key=f"project_btn_{pid}",
                use_container_width=True,
                disabled=disabled,
        ):
            st.session_state.chat_project_select = pname
            fetch_history(force=True)
            rebuild_project_histories_from_server()
            st.rerun()

        st.sidebar.caption(status_label)
        if progress is not None and progress < 100:
            st.sidebar.progress(progress / 100.0)


def render_reset_box():
    st.sidebar.subheader("데이터 초기화")
    st.sidebar.caption("Qdrant + SQLite 전체 데이터 삭제")

    if not st.session_state.show_reset_confirm:
        if st.sidebar.button("전체 Reset", type="secondary", use_container_width=True):
            st.session_state.show_reset_confirm = True
            st.rerun()
        return

    st.sidebar.warning("정말 초기화하려면 아래 버튼을 누르세요.")
    col1, col2 = st.sidebar.columns(2)

    with col1:
        if st.button("RESET 실행", key="do_reset_btn", use_container_width=True):
            try:
                r = api_delete("/reset", params={"confirm_text": "RESET"}, timeout=120)
                r.raise_for_status()
                reset_local_state_after_reset()
                st.sidebar.success("초기화 완료")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"초기화 실패: {e}")

    with col2:
        if st.button("취소", key="cancel_reset_btn", use_container_width=True):
            st.session_state.show_reset_confirm = False
            st.rerun()


def upload_files_and_start_index(uploaded_files):
    if not uploaded_files:
        return

    st.session_state.uploading = True
    st.session_state.indexing = False
    st.session_state.active_job_id = None
    st.session_state.active_job_detail = None

    files_payload = []
    file_sig_parts = []

    for f in uploaded_files:
        file_bytes = f.getvalue()
        file_sig_parts.append(f"{f.name}:{len(file_bytes)}")
        files_payload.append(("files", (f.name, file_bytes, f.type or "application/octet-stream")))

    st.session_state.last_uploaded_file_sig = "|".join(file_sig_parts)

    try:
        upload_resp = api_post("/upload", files=files_payload, timeout=300)
        upload_resp.raise_for_status()
        upload_data = upload_resp.json()
        st.session_state.last_upload_result = upload_data
        targets = upload_data.get("targets", [])
        st.session_state.last_uploaded_targets = targets
        st.session_state.uploading = False

        if not targets:
            st.error("업로드는 완료됐지만 인덱싱 대상이 없습니다.")
            return

        project_name = normalize_project_name(targets[0].get("project_name"))
        st.session_state.latest_project_name = project_name

        index_resp = api_post("/index/jobs", json_data={"targets": targets}, timeout=60)
        index_resp.raise_for_status()
        job_data = index_resp.json()

        st.session_state.active_job_id = job_data.get("job_id")
        st.session_state.indexing = True

        fetch_projects(force=True)
        fetch_index_jobs(force=True)

    except Exception as e:
        st.session_state.uploading = False
        st.session_state.indexing = False
        st.session_state.active_job_id = None
        st.session_state.active_job_detail = None
        st.error(f"업로드/인덱싱 시작 실패: {e}")


def process_pending_upload():
    pending = st.session_state.get("pending_upload")
    pending_sig = st.session_state.get("pending_upload_sig", "")

    if not pending:
        return

    if pending_sig == st.session_state.get("last_uploaded_file_sig", ""):
        st.session_state.pending_upload = None
        st.session_state.pending_upload_sig = ""
        return

    upload_files_and_start_index(pending)
    st.session_state.pending_upload = None
    st.session_state.pending_upload_sig = ""
    st.rerun()


def refresh_active_job():
    active_job_id = st.session_state.get("active_job_id")
    if not active_job_id:
        return

    detail = fetch_index_job_detail(active_job_id)
    if not detail:
        return

    st.session_state.active_job_detail = detail
    status = (detail.get("status") or "").lower()

    fetch_index_jobs(force=True)
    fetch_projects(force=True)

    if status in {"completed", "failed"}:
        st.session_state.indexing = False
        st.session_state.uploading = False
        if status == "completed":
            st.session_state.latest_project_name = normalize_project_name(detail.get("project_name"))
    else:
        st.session_state.indexing = True


def trigger_live_refresh():
    if st.session_state.get("uploading") or st.session_state.get("indexing"):
        st_autorefresh(interval=2000, key="live_job_refresh")


def render_upload_status_box():
    active_job = st.session_state.get("active_job_detail") or {}
    active_job_id = st.session_state.get("active_job_id")

    if st.session_state.get("uploading"):
        st.info("파일 업로드 중입니다...")

    if st.session_state.get("indexing") and active_job_id:
        pname = normalize_project_name(
            active_job.get("project_name") or st.session_state.get("latest_project_name")
        )
        progress = calc_job_progress(active_job)
        status = active_job.get("status") or "queued"
        message = active_job.get("message") or ""

        st.markdown(f"**현재 프로젝트:** {pname}")
        st.progress(progress / 100.0)
        st.caption(f"상태: {status} · 진행률: {progress}%")
        if message:
            st.caption(message)

    if not st.session_state.get("uploading") and not st.session_state.get("indexing"):
        latest = st.session_state.get("latest_project_name")
        if latest:
            st.success(f"{latest} 업로드/인덱싱 작업이 완료되었습니다.")


def render_upload_area():
    st.subheader("업로드")
    st.caption("파일을 선택하는 즉시 자동 업로드 및 인덱싱이 시작됩니다.")
    render_upload_status_box()

    uploader_key = f"auto_uploader_{st.session_state.uploader_nonce}"
    uploaded_files = st.file_uploader(
        "파일 선택",
        type=[
            "zip", "py", "java", "js", "ts", "sql", "sh", "txt", "md", "json",
            "xml", "yml", "yaml", "ini", "toml", "html", "htm", "css"
        ],
        accept_multiple_files=True,
        key=uploader_key,
        help="선택 즉시 업로드 및 인덱싱 시작",
        disabled=st.session_state.get("uploading") or st.session_state.get("indexing"),
    )

    if uploaded_files:
        file_sig = "|".join([f"{f.name}:{f.size}" for f in uploaded_files])

        if file_sig != st.session_state.get("last_uploaded_file_sig", ""):
            st.session_state.uploading = True
            st.session_state.indexing = False
            st.session_state.pending_upload = uploaded_files
            st.session_state.pending_upload_sig = file_sig
            st.rerun()


def save_server_history(project_name: str, question: str, answer: str):
    try:
        stored_question = f"[{project_name}] {question}"
        api_post("/history", json_data={"question": stored_question, "answer": answer}, timeout=20)
    except Exception:
        pass


def ask_backend(question: str, project_name: str | None) -> str:
    params = {
        "question": question,
        "top_k": 5,
        "extra_context": "",
    }
    if project_name and project_name != "전체":
        params["project_name"] = project_name

    chunks = []
    try:
        with api_get("/ask", params=params, timeout=300, stream=True) as r:
            if r.status_code >= 400:
                return f"백엔드 /ask 오류: HTTP {r.status_code} - {r.text}"

            for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
                if chunk:
                    chunks.append(chunk)

        answer = "".join(chunks).strip()
        if answer:
            return answer

        return "응답이 비어 있습니다. 프로젝트 인덱싱 상태와 Ollama 상태를 확인해주세요."

    except Exception as e:
        return (
            "프론트에서 /ask 스트리밍 처리 중 예외가 발생했습니다.\n\n"
            f"- 질문: {question}\n"
            f"- 선택 프로젝트: {project_name or '전체'}\n"
            f"- 원본 오류: {e}\n\n"
            "이 오류가 계속 뜨면 백엔드 /ask와 Ollama 연결 상태를 점검하세요."
        )


def render_chat_area():
    st.subheader("질문")
    selected_project = current_project_name()

    if selected_project == "전체":
        st.info("사이드바에서 프로젝트를 선택한 뒤 질문하세요. 선택한 프로젝트의 대화만 표시됩니다.")
        return

    st.caption(f"현재 프로젝트 공간: {selected_project}")

    visible_messages = get_visible_chat_messages()
    for msg in visible_messages:
        with st.chat_message("user" if msg["role"] == "user" else "assistant"):
            if msg["role"] == "assistant":
                render_answer(msg["content"])
            else:
                st.markdown(msg["content"])

    jobs = fetch_index_jobs(force=True)
    projects = fetch_projects(force=True)
    job_map = build_project_job_map(projects, jobs)
    job = job_map.get(selected_project)
    project_locked = not project_selectable(job)

    disabled_reason = None
    if st.session_state.get("uploading"):
        disabled_reason = "업로드 진행 중입니다."
    elif st.session_state.get("indexing") and project_locked:
        disabled_reason = "선택한 프로젝트는 아직 인덱싱 완료 전입니다."
    elif project_locked:
        disabled_reason = "선택한 프로젝트는 아직 인덱싱 완료 전입니다."

    if disabled_reason:
        st.info(disabled_reason)

    question = st.chat_input(
        "코드 구조, 흐름, DB, 호출관계 등을 질문하세요.",
        disabled=disabled_reason is not None,
    )

    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("답변 생성 중..."):
            answer = ask_backend(question, selected_project)
        render_answer(answer)

    local_ts = time.time()
    st.session_state.project_histories.setdefault(selected_project, [])
    st.session_state.project_histories[selected_project].append(
        {"role": "user", "content": question, "ts": local_ts}
    )
    st.session_state.project_histories[selected_project].append(
        {"role": "assistant", "content": answer, "ts": local_ts}
    )

    save_server_history(selected_project, question, answer)
    fetch_history(force=True)
    rebuild_project_histories_from_server()
    st.rerun()
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9


def bootstrap():
    fetch_system_status(force=True)
    fetch_projects(force=True)
    fetch_index_jobs(force=True)
    fetch_history(force=True)
    rebuild_project_histories_from_server()
    refresh_active_job()


<<<<<<< HEAD
    status = fetch_system_status(force=refresh_clicked)
=======
bootstrap()
process_pending_upload()
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9

st.title("🧠 IT-Smart CodeMind")
st.caption("자동 업로드/자동 인덱싱 · 프로젝트 선택형 대화")

with st.sidebar:
    render_system_status()
    st.divider()
    render_sidebar_projects()
    st.divider()
    render_reset_box()

<<<<<<< HEAD
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
=======
render_upload_area()
st.divider()
render_chat_area()

trigger_live_refresh()
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
