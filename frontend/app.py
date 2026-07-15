import os
import re
import time
from datetime import datetime
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components
import extra_streamlit_components as stx
import logging

from streamlit_autorefresh import st_autorefresh

BACKEND_URL = os.getenv("FASTAPI_URL", "http://codeMind-backend:8000")
COOKIE_KEY      = "codeMind_user_id"
COOKIE_MAX_AGE  = 60 * 60 * 24  # 1일

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="IT-Smart CodeMind",
    page_icon="🧠",
    layout="wide",
)

# ─────────────────────────────────────────────
# CookieManager
# · set_page_config 직후, 다른 st.* 보다 먼저
# · 첫 렌더링에서 JS 로 쿠키를 읽어오므로 값은 두 번째 실행부터 유효
# ─────────────────────────────────────────────
_cookie_mgr = stx.CookieManager(key="codeMind_cookie_mgr")


def cookie_get(key: str) -> str:
    try:
        val = (_cookie_mgr.get(key) or "").strip()
        # logger.info("[COOKIE] get key=%s → '%s'", key, val)
        # logger.info("============================================================")
        return val
    except Exception as e:
        logger.warning("[COOKIE] get failed key=%s: %s", key, e)
        return ""


def cookie_set(key: str, value: str) -> None:
    try:
        _cookie_mgr.set(key, value, max_age=COOKIE_MAX_AGE)
        # logger.info("[COOKIE] set key=%s value='%s'", key, value)
        # logger.info("============================================================")
    except Exception as e:
        logger.warning("[COOKIE] set failed key=%s: %s", key, e)


def cookie_delete(key: str) -> None:
    """
    extra-streamlit-components 의 delete/set 은 브라우저에 즉시 반영되지 않음.
    가능한 모든 방법을 시도하되, 실제 삭제 보장은 _logged_out 플래그로 보완.
    """
    for method_name, fn in [
        ("set_empty_max_age_0",  lambda: _cookie_mgr.set(key, "", max_age=0)),
        ("delete",               lambda: _cookie_mgr.delete(key)),
    ]:
        try:
            fn()
            # logger.info("[COOKIE] delete step '%s' key=%s ok", method_name, key)
            # logger.info("============================================================")
        except Exception as e:
            logger.warning("[COOKIE] delete step '%s' key=%s failed: %s", method_name, key, e)



# ─────────────────────────────────────────────
# session_state 초기화
# ─────────────────────────────────────────────
def init_session_state():
    defaults = {
        "user_id": None,
        # "admin_role": False,
        "projects": [],
        "projects_error": None,
        "system_status": None,
        "system_status_error": None,
        "index_jobs": [],
        "index_job_error": None,
        "history_items": [],
        "history_error": None,
        "latest_project_name": None,
        "chat_project_select": None,
        "chat_project_id": None,
        "active_job_id": None,
        "active_job_detail": None,
        "uploading": False,
        "indexing": False,
        "show_reset_confirm": False,
        "project_histories": {},
        "uploader_nonce": 0,
        "pending_upload": None,
        "duplicate_pending": None,
        "upload_items": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value



# ─────────────────────────────────────────────
# 로그인 / 로그아웃
# ─────────────────────────────────────────────
def verify_user_id(user_id: str) -> bool:
    """백엔드 /users/verify 로 user_id 존재 여부를 확인하고, 없으면 생성합니다."""
    r = requests.get(
        f"{BACKEND_URL}/users/verify",
        params={"user_id": user_id},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("exists", False)


def do_login(uid: str) -> None:
    """로그인 처리: session + 쿠키에 user_id 저장. _logged_out 플래그 해제."""
    logger.info("[LOGIN] do_login uid='%s'", uid)
    st.session_state.user_id = uid
    # st.session_state.admin_role = (uid.lower() == "admin")
    st.session_state["_logged_out"] = False   # 로그아웃 차단 플래그 해제
    # logger.info("[LOGIN] admin_role=%s _logged_out=False", st.session_state.admin_role)
    cookie_set(COOKIE_KEY, uid)


def do_logout() -> None:
    """
    로그아웃:
    1. 쿠키 삭제 시도
    2. session 에서 user_id / admin_role 즉시 제거
    3. _logged_out 플래그를 session 에 보존 — 쿠키가 남아있어도 복원 차단
    4. _logout_pending 으로 세션 초기화 트리거
    """
    # logger.info("[LOGOUT] do_logout 시작 current_user='%s'", st.session_state.get("user_id"))
    cookie_delete(COOKIE_KEY)
    st.session_state.user_id = None
    # st.session_state.admin_role = False
    st.session_state["_logged_out"] = True   # 쿠키 복원 차단 플래그
    st.session_state["_logout_pending"] = True
    # logger.info("[LOGOUT] _logout_pending=True _logged_out=True, rerun")
    # logger.info("============================================================")
    st.rerun()


def render_login_page():
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        st.markdown("## 🧠 IT-Smart CodeMind")
        st.markdown("##### 사용자 ID를 입력하세요")
        st.caption("등록되지 않은 사용자는 신규 등록됩니다.")
        st.divider()

        with st.form("login_form", clear_on_submit=False):
            user_id_input = st.text_input(
                "User ID",
                placeholder="예: user",
                max_chars=100,
            )
            submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")

        if submitted:
            uid = (user_id_input or "").strip()
            if not uid:
                st.error("User ID를 입력해주세요.")
            else:
                with st.spinner("사용자 확인 중..."):
                    try:
                        exists = verify_user_id(uid)
                    except Exception as e:
                        st.error(f"사용자 조회 오류: {e}")
                        return
                logger.info("[LOGIN] form submit uid='%s' exists=%s", uid, exists)
                do_login(uid)
                msg = f"'{uid}' 로 로그인되었습니다." if exists else f"'{uid}' 가 신규 등록되었습니다."
                st.success(msg)
                time.sleep(0.5)
                st.rerun()


def get_headers() -> dict[str, str]:
    return {"X-User-Id": st.session_state.user_id}


# ─────────────────────────────────────────────
# API 호출 헬퍼
# ─────────────────────────────────────────────
def api_get(path: str, params: dict | None = None, timeout: int = 30, stream: bool = False):
    return requests.get(
        f"{BACKEND_URL}{path}",
        headers=get_headers(),
        params=params,
        timeout=timeout,
        stream=stream,
    )


def api_post(path: str, json_data: Any = None, files=None, timeout: int = 120, stream: bool = False):
    return requests.post(
        f"{BACKEND_URL}{path}",
        headers=get_headers(),
        json=json_data,
        files=files,
        timeout=timeout,
        stream=stream,
    )


def api_delete(path: str, params: dict | None = None, timeout: int = 30):
    return requests.delete(
        f"{BACKEND_URL}{path}",
        headers=get_headers(),
        params=params,
        timeout=timeout,
    )

# ─────────────────────────────────────────────
# Mermaid 코드 추출 및 렌더링
# ─────────────────────────────────────────────
def extract_mermaid_blocks(text: str) -> list[str]:
    if not text:
        return []
    return [
        match.strip()
        for match in re.findall(r"```mermaid\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    ]


def strip_mermaid_blocks(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"```mermaid\s*.*?```", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


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

    for index, block in enumerate(mermaid_blocks, start=1):
        st.caption(f"Diagram {index}")
        render_mermaid(block)


# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────

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

    for fmt in patterns:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            pass

    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return time.time()


def normalize_project_name(name: str | None) -> str:
    return (name or "").strip() or "이름없음"


def current_project_name() -> str:
    return st.session_state.get("chat_project_select", "")


def current_project_id() -> str | None:
    return st.session_state.get("chat_project_id")


def project_key(project_id: str | None) -> str:
    return (project_id or "").strip() or "__global__"


def project_name_by_id(project_id: str | None) -> str:
    if not project_id:
        return "업로드"

    for project in st.session_state.get("projects", []):
        if (project.get("project_id") or "").strip() == project_id:
            return normalize_project_name(project.get("project_name"))

    return normalize_project_name(project_id)


def dedupe_projects(projects: list[dict]) -> list[dict]:
    by_project_id: dict[str, dict] = {}

    for project in projects:
        project_id = (project.get("project_id") or "").strip()
        if not project_id:
            continue

        existing = by_project_id.get(project_id)
        if not existing:
            by_project_id[project_id] = project
            continue

        old_uploaded_at = existing.get("uploaded_at") or ""
        new_uploaded_at = project.get("uploaded_at") or ""

        if new_uploaded_at >= old_uploaded_at:
            by_project_id[project_id] = project

    return sorted(
        by_project_id.values(),
        key=lambda item: item.get("uploaded_at") or "",
        reverse=True,
    )

def stem_filename(filename: str) -> str:
    """확장자 제거"""
    return filename.rsplit(".", 1)[0] if "." in filename else filename


# ─────────────────────────────────────────────
# 서버 데이터 조회
# ─────────────────────────────────────────────

def fetch_system_status(force: bool = False):
    if st.session_state.system_status is not None and not force:
        return st.session_state.system_status

    try:
        response = api_get("/status", timeout=15)
        response.raise_for_status()
        st.session_state.system_status = response.json()
        st.session_state.system_status_error = None
    except Exception as error:
        st.session_state.system_status = None
        st.session_state.system_status_error = str(error)

    return st.session_state.system_status


def fetch_projects(force: bool = False):
    if st.session_state.projects and not force:
        return st.session_state.projects

    try:
        response = api_get("/projects", timeout=20)
        response.raise_for_status()
        data = response.json()
        raw_projects = data.get("projects", [])
        st.session_state.projects = dedupe_projects(raw_projects)
        st.session_state.projects_error = None

        valid_ids = {None, ""} | {
            (project.get("project_id") or "").strip()
            for project in st.session_state.projects
        }

        if (st.session_state.chat_project_id or "") not in valid_ids:
            st.session_state.chat_project_select = None
            st.session_state.chat_project_id = None

    except Exception as error:
        st.session_state.projects = []
        st.session_state.projects_error = str(error)

    return st.session_state.projects


def fetch_index_jobs(force: bool = False):
    if st.session_state.index_jobs and not force:
        return st.session_state.index_jobs
    try:
        response = api_get("/index-jobs", params={"limit": 50}, timeout=20)
        response.raise_for_status()
        data = response.json()
        st.session_state.index_jobs = data.get("jobs", [])
        st.session_state.index_job_error = None
    except Exception as error:
        st.session_state.index_jobs = []
        st.session_state.index_job_error = str(error)
    return st.session_state.index_jobs


def fetch_index_job_detail(job_id: str):
    try:
        response = api_get(f"/index-jobs/{job_id}", timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def fetch_history(project_id: str | None = None, force: bool = False):
    """
    project_id 기준으로 히스토리를 조회합니다.
    """
    if not project_id:
        logger.info("[HISTORY] fetch_history project_id 없음 → 조회 생략")
        st.session_state.history_items = []
        return []

    cache_key = f"_hist_loaded_{project_id}"
    if not force and st.session_state.get(cache_key):
        logger.info("[HISTORY] fetch_history cache hit project_id=%s", project_id)
        return st.session_state.history_items

    logger.info("[HISTORY] fetch_history 요청 project_id=%s force=%s", project_id, force)
    try:
        r = api_get("/history", params={"project_id": project_id, "limit": 300}, timeout=20)
        logger.info("[HISTORY] 응답 status=%d", r.status_code)
        r.raise_for_status()
        rows = r.json().get("history", [])
        logger.info("[HISTORY] 수신 count=%d project_id=%s", len(rows), project_id)
        st.session_state.history_items = rows
        st.session_state.history_error = None
        st.session_state[cache_key] = True
    except Exception as e:
        logger.warning("[HISTORY] fetch_history 실패 project_id=%s error=%s", project_id, e)
        st.session_state.history_items = []
        st.session_state.history_error = str(e)
    return st.session_state.history_items


def rebuild_project_histories_from_server():
    """
    history_items(현재 선택된 project_id 의 히스토리)를
    project_histories[project_key] 에 반영합니다.
    """
    histories = st.session_state.get("history_items") or []
    pid = current_project_id()
    key = project_key(pid)

    logger.info("[HISTORY] rebuild project_id=%s key=%s items=%d", pid, key, len(histories))

    messages: list[dict] = []
    for item in reversed(histories):
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        ts = parse_created_at_to_ts(item.get("created_at"))
        if question:
            messages.append({"role": "user", "content": question, "ts": ts})
        if answer:
            messages.append({"role": "assistant", "content": answer, "ts": ts})

    st.session_state.project_histories[key] = messages
    # logger.info("[HISTORY] rebuild 완료 key=%s messages=%d", key, len(messages))
    # logger.info("============================================================")


# ─────────────────────────────────────────────
# Job / 상태 헬퍼
# ─────────────────────────────────────────────

def calc_job_progress(job: dict) -> int:
    total_targets = int(job.get("total_targets") or 0)
    processed_targets = int(job.get("processed_targets") or 0)
    status = (job.get("status") or "").lower()

    if status == "completed":
        return 100
    if total_targets <= 0:
        return 0

    pct = int((processed_targets / total_targets) * 100)
    return max(0, min(99 if status in {"queued", "running"} and processed_targets < total_targets else 100, pct))


def build_project_job_map(projects: list[dict], jobs: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for project in projects:
        project_id = (project.get("project_id") or "").strip()
        if not project_id:
            continue
        matched = sorted(
            [job for job in jobs if (job.get("project_id") or "").strip() == project_id],
            key=lambda job: (
                job.get("updated_at") or "",
                job.get("created_at") or ""),
            reverse=True,
        )
        if matched:
            result[project_id] = matched[0]
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
    selected_project_id = current_project_id()

    if not selected_project_id:
        return st.session_state.project_histories.get("__all__", [])

    return st.session_state.project_histories.get(project_key(selected_project_id), [])


def reset_local_state_after_reset():
    for k in ("projects", "index_jobs", "history_items", "project_histories"):
        st.session_state[k] = [] if isinstance(st.session_state.get(k), list) else {}
    for k in ("projects_error", "index_job_error", "history_error", "latest_project_name",
               "active_job_id", "active_job_detail",
               "system_status", "system_status_error"):
        st.session_state[k] = None
    st.session_state.chat_project_select = None
    st.session_state.chat_project_id = None
    st.session_state.uploading = False
    st.session_state.indexing = False
    st.session_state.show_reset_confirm = False
    st.session_state.pending_upload = None
    st.session_state.uploader_nonce += 1


# ─────────────────────────────────────────────
# 사이드바 렌더링
# ─────────────────────────────────────────────

def render_system_status():
    st.sidebar.subheader("시스템 상태")
    status = fetch_system_status()
    if not status:
        err = st.session_state.system_status_error or "상태 조회 실패"
        st.sidebar.error(err)
        return

    overall = status.get("overall", "unknown")
    rag_initialized = status.get("rag_initialized", False)

    if overall == "healthy":
        st.sidebar.success("정상")
    elif overall == "degraded":
        st.sidebar.warning("부분 장애")
    else:
        st.sidebar.error("비정상")

    st.sidebar.caption(f"RAG 초기화: {'완료' if rag_initialized else '미완료'}")

    for service in status.get("services", []):
        name = service.get("name", "-")
        service_status = service.get("status", "unknown")
        message = service.get("message", "")
        icon = "🟢" if service_status == "running" else ("🟡" if service_status == "degraded" else "🔴")
        st.sidebar.caption(f"{icon} {name} - {service_status}")
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

    if st.sidebar.button("프로젝트 업로드", key="upload_projects_btn", use_container_width=True):
        st.session_state.chat_project_select = None
        st.session_state.chat_project_id = None
        st.rerun()

    current_project_id_value = current_project_id()

    for project in projects:
        project_name = normalize_project_name(project.get("project_name"))
        project_id = (project.get("project_id") or "").strip()
        job = project_job_map.get(project_id)

        disabled = not project_selectable(job)
        status_label = get_project_status_label(job)
        progress = calc_job_progress(job) if job else None
        selected = current_project_id_value == project_id

        label = f"📁 {project_name}"
        if selected:
            label += " ✅"

        if st.sidebar.button(
                label,
                key=f"project_btn_{project_id}",
                use_container_width=True,
                disabled=disabled,
        ):
            st.session_state.chat_project_select = project_name
            st.session_state.chat_project_id = project_id
            # 해당 프로젝트 히스토리만 재로드
            fetch_history(project_id=project_id, force=True)
            rebuild_project_histories_from_server()
            st.rerun()

        st.sidebar.caption(status_label)
        if progress is not None and progress < 100:
            st.sidebar.progress(progress / 100.0)


def render_user_box():
    """사이드바 하단: 로그인 사용자 정보 + 로그아웃 버튼."""
    st.sidebar.divider()
    uid = st.session_state.get("user_id", "")
    st.sidebar.caption(f"👤 사용자 : **{uid}**")
    if st.sidebar.button("🔓 로그아웃 / 계정 전환", key="logout_btn", use_container_width=True):
        do_logout()


# ─────────────────────────────────────────────
# 업로드 / 인덱싱
# ─────────────────────────────────────────────

def _start_index_job(targets: list):
    """백엔드 /index-jobs 를 호출하고 active_job_id 를 저장합니다."""
    if not targets:
        return
    project_name = normalize_project_name(targets[0].get("project_name"))
    st.session_state.latest_project_name = project_name
    try:
        r = api_post("/index-jobs", json_data={"targets": targets}, timeout=60)
        r.raise_for_status()
        st.session_state.active_job_id = r.json().get("job_id")
        st.session_state.indexing = True
    except Exception as e:
        st.error(f"인덱싱 작업 시작 실패: {e}")
        st.session_state.uploading = False
        st.session_state.indexing = False
        st.session_state.pending_upload = None
        st.session_state.latest_project_name = None
        return
    fetch_projects(force=True)
    fetch_index_jobs(force=True)


def upload_files_and_start_index(files_payload: list):
    """/upload 호출 후 즉시 인덱싱을 시작합니다."""
    try:
        r = api_post("/upload", files=files_payload, timeout=300)
        r.raise_for_status()
        data = r.json()
        targets = data.get("targets", [])
        if not targets:
            st.error("업로드는 완료됐지만 인덱싱 대상이 없습니다.")
            st.session_state.pending_upload = None
            return
        st.session_state.uploading = True
        _start_index_job(targets)
    except Exception as e:
        st.session_state.uploading = False
        st.session_state.indexing = False
        st.session_state.pending_upload = None
        st.error(f"업로드 실패: {e}")


def start_upload_process(uploaded_files): # 업로드할 파일을 가지고 왔음.
    """
    파일 업로드를 시작합니다.
    - 동명 프로젝트가 존재하면 → 중복 확인 다이얼로그로 위임
    - 그 외 → 바로 업로드 + 인덱싱
    """
    if not uploaded_files:
        return

    files_payload: list = []

    for uploaded_file in uploaded_files: # 업로드 파일 목록을 순회하면서
        file_bytes = uploaded_file.getvalue()
        files_payload.append(
            ("files", (uploaded_file.name, file_bytes, uploaded_file.type or "application/octet-stream"))
        )

    # 파일 사전 중복 체크
    if len(uploaded_files) > 1: # 다중 파일
        pass
    else: # 단일 파일
        project_name = stem_filename(uploaded_files[0].name) # 확장자를 제거
        try:
            r = api_get(f"/projects/{project_name}", timeout=10) # SQLite3 조회
            if r.status_code == 200:
                old_pid = r.json().get("project_id")
                if old_pid:
                    # 동명 프로젝트 존재 → 확인 다이얼로그로
                    st.session_state.duplicate_pending = {
                        "old_project_id": old_pid,
                        "project_name": project_name,
                    }
                    st.session_state.upload_items = files_payload
                    return
        except Exception as e:
            logger.warning("중복 체크 오류(무시하고 진행): %s", e)

    # 중복 없음 → 바로 업로드
    upload_files_and_start_index(files_payload)


def render_duplicate_confirm_dialog() -> bool:
    """중복 프로젝트 확인 다이얼로그. 표시 중이면 True 반환."""
    pending = st.session_state.get("duplicate_pending")
    if not pending:
        return False

    project_name = pending["project_name"]
    st.warning(
        f"⚠️ **'{project_name}'** 프로젝트가 이미 존재합니다.\n\n"
        "확인 시 기존 소스·벡터·히스토리를 모두 삭제하고 새 소스로 교체합니다."
    )

    col_info, col_btn = st.columns([3, 2])
    with col_info:
        st.text_input("프로젝트명", value=project_name, disabled=True)
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ 확인", key="dup_confirm_btn", use_container_width=True, type="primary"):
                _resolve_duplicate(pending) # 중복일 때 변수에 담아두었던 정보를 전달
        with c2:
            if st.button("❌ 취소", key="dup_cancel_btn", use_container_width=True):
                st.session_state.duplicate_pending = None
                st.session_state.pending_upload = None
                st.session_state.uploader_nonce += 1
                st.rerun()
    return True


def _resolve_duplicate(pending: dict):
    """
    중복 확인 버튼 클릭 시:
    1. /projects/{old_pid} 로 구 데이터(SQLite + Qdrant) 전부 삭제
    2. 세션 정리
    3. 새 파일 업로드 + 인덱싱 시작
    4. rerun
    """
    old_pid = pending["old_project_id"]

    try:
        r = api_delete(f"/projects/{old_pid}", timeout=30)
        r.raise_for_status()
        logger.info("[UPLOAD] duplicate 처리 완료 old_pid=%s result=%s", old_pid, r.json())
    except Exception as e:
        st.error(f"프로젝트 중복 처리 실패: {e}")
        return

    # 세션 정리
    st.session_state.duplicate_pending = None
    st.session_state.project_histories.pop(project_key(old_pid), None)

    # 현재 선택 프로젝트가 교체 대상이면 초기화
    if st.session_state.chat_project_id == old_pid:
        st.session_state.chat_project_select = None
        st.session_state.chat_project_id = None

    files_payload = st.session_state.upload_items
    st.session_state.upload_items = []

    if files_payload:
        upload_files_and_start_index(files_payload)

    st.rerun()


def process_pending_upload():
    """
    rerun 마다 호출됨. pending_upload 가 있을 때만 업로드 처리를 시작합니다.
    - pending_upload 를 먼저 None 으로 초기화해서 다음 rerun 에서 재진입 방지
    - 업로드 완료 후 uploader_nonce 를 올려 파일 위젯 상태를 완전히 초기화
    """
    pending_upload = st.session_state.get("pending_upload")
    if not pending_upload:
        return

    logger.info("[UPLOAD] process_pending_upload 진입 files=%d", len(pending_upload))

    # ① pending 을 먼저 초기화 — 이후 rerun 에서 재진입 차단
    st.session_state.pending_upload = None

    # ② 업로드 처리 (중복 감지 시 duplicate_pending 설정하고 return)
    start_upload_process(pending_upload)

    # ③ 업로드 위젯 key 교체 — Streamlit 위젯이 파일 상태를 유지하므로
    #    nonce 를 올려야 다음 rerun 에서 위젯이 빈 상태로 렌더링됨
    st.session_state.uploader_nonce += 1

    # ④ rerun: 중복 다이얼로그 표시 또는 인덱싱 진행 상태 반영
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

    total_targets = int(detail.get("total_targets") or 0)
    processed_targets = int(detail.get("processed_targets") or 0)
    progress = calc_job_progress(detail)

    if status in {"completed", "failed"}:
        st.session_state.indexing = False
        st.session_state.uploading = False
        if status == "completed":
            st.session_state.latest_project_name = normalize_project_name(detail.get("project_name"))
        return

    if total_targets > 0 and processed_targets >= total_targets and progress >= 100:
        st.session_state.indexing = False
        st.session_state.uploading = False
        st.session_state.latest_project_name = normalize_project_name(detail.get("project_name"))
        return

    st.session_state.indexing = True


def render_upload_area():
    st.subheader("업로드")
    st.caption("파일을 선택하는 즉시 자동 업로드 및 인덱싱이 시작됩니다.")

    uploader_key = f"auto_uploader_{st.session_state.uploader_nonce}"
    uploaded_files = st.file_uploader(
        "파일 선택",
        type=[
            "zip"
            # , "py", "java", "js", "ts", "sql", "sh", "txt", "md", "json",
            # "xml", "yml", "yaml", "ini", "toml", "html", "htm", "css"
        ],
        accept_multiple_files=True,
        key=uploader_key,
        help="선택 즉시 업로드 및 인덱싱 시작",
    )

    if uploaded_files:
        st.session_state.pending_upload = uploaded_files
        logger.info("upload_files 존재")

        st.rerun()


# ─────────────────────────────────────────────
# 채팅
# ─────────────────────────────────────────────

def ask_backend(question: str, project_name: str | None, project_id: str | None) -> str:
    payload = {
        "question": question,
        "top_k": 5,
        "extra_context": "",
    }

    if project_id:
        payload["project_id"] = project_id
    elif project_name:
        payload["project_name"] = project_name

    chunks: list[str] = []
    try:
        with api_post("/ask", json_data=payload, timeout=300, stream=True) as response:
            if response.status_code >= 400:
                return f"백엔드 /ask 오류: HTTP {response.status_code} - {response.text}"

            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                if chunk:
                    chunks.append(chunk)

        answer = "".join(chunks).strip()
        if answer:
            return answer

        return "응답이 비어 있습니다. 프로젝트 인덱싱 상태와 Ollama 상태를 확인해주세요."

    except Exception as error:
        return (
            "프론트에서 /ask 스트리밍 처리 중 예외가 발생했습니다.\n\n"
            f"- 질문: {question}\n"
            f"- 선택 프로젝트: {project_name}\n"
            f"- 프로젝트 ID: {project_id or '없음'}\n"
            f"- 원본 오류: {error}\n\n"
            "이 오류가 계속 뜨면 백엔드 /ask와 Ollama 연결 상태를 점검하세요."
        )


def _clear_project_session(pid: str):
    """삭제된 프로젝트를 세션에서 제거하고 업로드 화면으로 돌아갑니다."""
    key = project_key(pid)
    st.session_state.project_histories.pop(key, None)
    st.session_state.projects = [
        p for p in st.session_state.projects
        if (p.get("project_id") or "").strip() != pid
    ]
    st.session_state.index_jobs = [
        j for j in st.session_state.index_jobs
        if (j.get("project_id") or "").strip() != pid
    ]
    if st.session_state.chat_project_id == pid:
        st.session_state.chat_project_select = None
        st.session_state.chat_project_id = None
    st.session_state.history_items = []


def render_chat_area():
    pid = current_project_id()
    pname = current_project_name()

    # ── 헤더: 제목 + 삭제 버튼 ───────────────────────────────
    col_title, col_btn = st.columns([4, 1])
    with col_title:
        st.subheader("질문")

    with col_btn:
        # 프로젝트가 선택된 경우에만 삭제 버튼 표시
        if pid:
            confirm_key = f"delete_confirm_{pid}"

            if st.button("🗑 프로젝트 삭제", key="del_action_btn", use_container_width=True):
                # 버튼 클릭 시 confirm 플래그 토글
                st.session_state[confirm_key] = not st.session_state.get(confirm_key, False)
                st.rerun()

    # ── 삭제 확인 다이얼로그 ─────────────────────────────────
    if pid:
        confirm_key = f"delete_confirm_{pid}"
        if st.session_state.get(confirm_key, False):
            st.warning(
                f"⚠️ **'{pname}'** 프로젝트의 모든 데이터(소스·벡터·히스토리·인덱스)를 삭제합니다.\n\n"
                "이 작업은 되돌릴 수 없습니다."
            )

            c1, c2, _ = st.columns([1, 1, 3])
            with c1:
                if st.button("✅ 확인", key="del_ok_btn", type="primary", use_container_width=True):
                    st.session_state[confirm_key] = False
                    try:
                        r = api_delete(
                            f"/projects/{pid}",
                            timeout=30,
                        )
                        r.raise_for_status()
                        _clear_project_session(pid)
                        st.success(f"'{pname}' 프로젝트가 삭제되었습니다.")
                    except Exception as e:
                        st.error(f"프로젝트 삭제 실패: {e}")
                    time.sleep(0.8)
                    st.rerun()
            with c2:
                if st.button("❌ 취소", key="del_cancel_btn", use_container_width=True):
                    st.session_state[confirm_key] = False
                    st.rerun()

    # ── 프로젝트 캡션 ────────────────────────────────────────
    if pid:
        st.caption(f"현재 프로젝트 공간: {project_name_by_id(pid)}")

    # ── 메시지 렌더링 ────────────────────────────────────────
    for msg in get_visible_chat_messages():
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                render_answer(msg["content"])
            else:
                st.markdown(msg["content"])

    if not pid:
        st.info("사이드바에서 프로젝트를 선택한 뒤 질문하세요.")
        return

    # ── 채팅 입력 ────────────────────────────────────────────

    question = st.chat_input(
        "코드 구조, 흐름, DB, 호출관계 등을 질문하세요.",
        # disabled=disabled_reason is not None,
    )

    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("답변 생성 중..."):
            answer = ask_backend(question, pname, pid)
        render_answer(answer)

    # 로컬 session 에 즉시 반영 (rerun 전 화면 유지)
    ts = time.time()
    key = project_key(pid)

    st.session_state.project_histories.setdefault(key, [])
    st.session_state.project_histories[key].append(
        {"role": "user", "content": question, "ts": ts}
    )
    st.session_state.project_histories[key].append(
        {"role": "assistant", "content": answer, "ts": ts}
    )

    # 서버 히스토리와 동기화 후 rerun
    fetch_history(project_id=pid, force=True)
    rebuild_project_histories_from_server()
    st.rerun()


# ─────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────

def bootstrap():
    fetch_system_status(force=True)
    fetch_projects(force=True)
    fetch_index_jobs(force=True)
    rebuild_project_histories_from_server()
    refresh_active_job()


def trigger_live_refresh():
    if st.session_state.get("uploading") or st.session_state.get("indexing"):
        st_autorefresh(interval=2000, key="live_job_refresh")


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────

# ① 로그아웃 플래그 처리
#    세션 전체 초기화 후 _logged_out 플래그를 재주입해서
#    쿠키가 아직 브라우저에 남아있어도 복원 경로(③)를 차단.
if st.session_state.pop("_logout_pending", False):
    # logger.info("[LOGOUT] _logout_pending 처리 — 세션 전체 초기화")
    cookie_val_at_logout = cookie_get(COOKIE_KEY)
    # logger.info("[LOGOUT] 이 시점 쿠키 값(삭제 미반영 가능): '%s'", cookie_val_at_logout)
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    init_session_state()
    st.session_state["_logged_out"] = True   # 재주입: 쿠키 복원 차단 유지
    # logger.info("[LOGOUT] 로그인 화면으로 이동 (_logged_out=True 유지)")
    # logger.info("============================================================")
    render_login_page()
    st.stop()

# ② session_state 기본값 설정
init_session_state()

# ③ user_id 확보
#    1순위: session_state (로그인 직후 or 이전 rerun 에서 복원된 경우)
#    2순위: 쿠키 — 단, _logged_out 플래그가 있으면 차단 (쿠키 삭제 미반영 대응)

if not st.session_state.get("user_id"):
    if st.session_state.get("_logged_out"):
        # logger.info("[AUTH] _logged_out=True → 쿠키 복원 차단, 로그인 화면")
        render_login_page()
        st.stop()

    uid_from_cookie = cookie_get(COOKIE_KEY)
    # logger.info("[AUTH] 쿠키 복원 시도 → '%s'", uid_from_cookie)
    if uid_from_cookie:
        st.session_state.user_id = uid_from_cookie
        # st.session_state.admin_role = (uid_from_cookie.lower() == "admin")
        # logger.info("[AUTH] 쿠키 복원 완료 user_id='%s' admin_role=%s",
                    # uid_from_cookie, st.session_state.admin_role)
    else:
        # logger.info("[AUTH] 쿠키 없음 → 로그인 화면")
        render_login_page()
        st.stop()

# logger.info("[AUTH] 최종 user_id='%s' admin_role=%s",
            # st.session_state.get("user_id"), st.session_state.get("admin_role"))

bootstrap()
process_pending_upload()

st.title("🧠 IT-Smart CodeMind")
st.caption(f"자동 업로드/자동 인덱싱 · 프로젝트 선택형 대화 · 사용자: {st.session_state.user_id}")

with st.sidebar:
    render_system_status()
    st.divider()
    render_sidebar_projects()
    render_user_box()   # 항상 최하단에 위치

if not st.session_state.get("chat_project_select"):
    render_upload_area()

    st.divider()

    if render_duplicate_confirm_dialog():
        st.stop()

else:
    render_chat_area()

trigger_live_refresh()
