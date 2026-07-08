import os
import re
import time
from datetime import datetime
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components
import logging

from streamlit_autorefresh import st_autorefresh

BACKEND_URL = os.getenv("FASTAPI_URL", "http://codeMind-backend:8000")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="IT-Smart CodeMind",
    page_icon="🧠",
    layout="wide",
)


# ─────────────────────────────────────────────
# session_state 초기화
# ─────────────────────────────────────────────

def init_session_state():
    defaults = {
        "user_id": None,
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
        "chat_project_id": None,
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
        "duplicate_pending": None,   # {old_project_id, project_name}
        "upload_items": [],          # 중복 확인 후 업로드할 파일 payload
        "admin_role": False,         # admin 로그인 여부
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

# ─────────────────────────────────────────────
# session_state 초기화
# ─────────────────────────────────────────────
init_session_state()


# ─────────────────────────────────────────────
# 로그인 게이트
# ─────────────────────────────────────────────
def verify_user_id(user_id: str) -> bool:
    """백엔드 /users/verify 로 user_id 존재 여부를 확인 후 없다면 생성합니다."""
    try:
        r = requests.get(
            f"{BACKEND_URL}/users/verify",
            params={"user_id": user_id},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("exists", False)
    except Exception as e:
        raise RuntimeError(f"사용자 조회 중 오류가 발생했습니다: {e}") from e


def render_login_page():
    st.markdown(
        """
        <style>
        .login-box {
            max-width: 420px;
            margin: 8rem auto 0 auto;
            padding: 2.5rem 2rem;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            background: #fafafa;
            box-shadow: 0 2px 12px rgba(0,0,0,0.07);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        st.markdown("## 🧠 IT-Smart CodeMind")
        st.markdown("##### 사용자 ID를 입력하세요")
        st.caption("등록되지 않은 사용자는 신규 등록됩니다.")
        st.divider()

        with st.form("login_form", clear_on_submit=False):
            user_id_input = st.text_input(
                "User ID",
                placeholder="예: 1234",
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
                    except RuntimeError as e:
                        st.error(str(e))
                        return
                msg = f"'{uid}' 로 로그인되었습니다." if exists else f"'{uid}' 가 신규 등록되었습니다."
                st.success(msg)
                st.session_state.user_id = uid
                if uid.lower() == 'admin':
                    st.session_state.admin_role = True
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
    return st.session_state.get("chat_project_select", "전체")


def current_project_id() -> str | None:
    return st.session_state.get("chat_project_id")


def project_key(project_id: str | None) -> str:
    return (project_id or "").strip() or "__global__"


def project_name_by_id(project_id: str | None) -> str:
    if not project_id:
        return "전체"

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
            st.session_state.chat_project_select = "전체"
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
    if st.session_state.history_items and not force:
        return st.session_state.history_items
    try:
        params: dict = {"limit": 300}
        if project_id:
            params["project_id"] = project_id
        response = api_get("/history", params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        st.session_state.history_items = data.get("history", [])
        st.session_state.history_error = None
    except Exception as error:
        st.session_state.history_items = []
        st.session_state.history_error = str(error)

    return st.session_state.history_items


def rebuild_project_histories_from_server():
    """history_items 를 project_id 기준으로 project_histories 에 재구성합니다."""
    histories = st.session_state.get("history_items") or []
    buckets: dict[str, list[dict]] = {}

    for item in reversed(histories):
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        ts = parse_created_at_to_ts(item.get("created_at"))
        key = project_key(item.get("project_id"))
        buckets.setdefault(key, [])
        if question:
            buckets[key].append({"role": "user", "content": question, "ts": ts})
        if answer:
            buckets[key].append({"role": "assistant", "content": answer, "ts": ts})

    all_msgs = sorted([m for msgs in buckets.values() for m in msgs], key=lambda x: x["ts"])
    buckets["__all__"] = all_msgs
    st.session_state.project_histories = buckets


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
    for k in ("projects", "index_jobs", "history_items", "last_uploaded_targets", "project_histories"):
        st.session_state[k] = [] if isinstance(st.session_state.get(k), list) else {}
    for k in ("projects_error", "index_job_error", "history_error", "latest_project_name",
               "active_job_id", "active_job_detail", "last_upload_result",
               "system_status", "system_status_error"):
        st.session_state[k] = None
    st.session_state.chat_project_select = "전체"
    st.session_state.chat_project_id = None
    st.session_state.uploading = False
    st.session_state.indexing = False
    st.session_state.last_uploaded_file_sig = ""
    st.session_state.show_reset_confirm = False
    st.session_state.pending_upload = None
    st.session_state.pending_upload_sig = ""
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

    if st.sidebar.button("전체 보기", key="all_projects_btn", use_container_width=True):
        st.session_state.chat_project_select = "전체"
        st.session_state.chat_project_id = None
        # 전체 히스토리 재로드
        fetch_history(project_id=None, force=True)
        rebuild_project_histories_from_server()
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
                response = api_delete("/reset", params={"confirm_text": "RESET"}, timeout=120)
                response.raise_for_status()
                reset_local_state_after_reset()
                st.sidebar.success("초기화 완료")
                time.sleep(1)
                st.rerun()
            except Exception as error:
                st.sidebar.error(f"초기화 실패: {error}")

    with col2:
        if st.button("취소", key="cancel_reset_btn", use_container_width=True):
            st.session_state.show_reset_confirm = False
            st.rerun()


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
        st.session_state.last_upload_result = data
        st.session_state.last_uploaded_targets = targets
        st.session_state.uploading = False
        if not targets:
            st.error("업로드는 완료됐지만 인덱싱 대상이 없습니다.")
            return
        _start_index_job(targets)
    except Exception as e:
        st.session_state.uploading = False
        st.session_state.indexing = False
        st.error(f"업로드 실패: {e}")


def start_upload_process(uploaded_files):
    """
    파일 업로드를 시작합니다.
    - 동명 프로젝트가 존재하면 → 중복 확인 다이얼로그로 위임
    - 그 외 → 바로 업로드 + 인덱싱
    """
    if not uploaded_files:
        return

    st.session_state.uploading = True
    st.session_state.indexing = False
    st.session_state.active_job_id = None
    st.session_state.active_job_detail = None

    files_payload: list = []
    file_sig_parts: list[str] = []

    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.getvalue()
        file_sig_parts.append(f"{uploaded_file.name}:{len(file_bytes)}")
        files_payload.append(
            ("files", (uploaded_file.name, file_bytes, uploaded_file.type or "application/octet-stream"))
        )

    st.session_state.last_uploaded_file_sig = "|".join(file_sig_parts)

    # 파일 사전 중복 체크
    if len(files_payload) == 1:
        project_name = stem_filename(uploaded_files[0].name)
        try:
            r = api_get(f"/projects/{project_name}", timeout=10)
            if r.status_code == 200:
                old_pid = r.json().get("project_id")
                if old_pid:
                    # 동명 프로젝트 존재 → 확인 다이얼로그로
                    st.session_state.duplicate_pending = {
                        "old_project_id": old_pid,
                        "project_name": project_name,
                    }
                    st.session_state.upload_items = files_payload
                    st.session_state.uploading = False
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
                _resolve_duplicate(pending)
        with c2:
            if st.button("❌ 취소", key="dup_cancel_btn", use_container_width=True):
                st.session_state.duplicate_pending = None
                st.session_state.upload_items = []
                st.session_state.uploading = False
                st.session_state.uploader_nonce += 1
                st.session_state.pending_upload = None
                st.session_state.pending_upload_sig = ""
                st.rerun()
    return True


def _resolve_duplicate(pending: dict):
    """
    중복 확인 버튼 클릭 시:
    1. /projects 로 구 project_id 의 SQLite + Qdrant 데이터 전부 삭제
    2. 세션 히스토리 초기화
    3. 새 파일 업로드 + 인덱싱 시작
    """
    old_pid = pending["old_project_id"]
    project_name = pending["project_name"]

    try:
        r = api_delete(f"/projects/{old_pid}", timeout=30)
        r.raise_for_status()
        logger.info("duplicate 처리 완료: %s", r.json())
    except Exception as e:
        st.error(f"프로젝트 중복 처리 실패: {e}")
        return

    # 세션 정리
    st.session_state.duplicate_pending = None
    key = project_key(old_pid)
    st.session_state.project_histories.pop(key, None)
    st.session_state.project_histories.pop(project_name, None)

    # 현재 선택 프로젝트가 교체 대상이면 초기화
    if st.session_state.chat_project_id == old_pid:
        st.session_state.chat_project_select = "전체"
        st.session_state.chat_project_id = None

    files_payload = st.session_state.upload_items
    st.session_state.upload_items = []

    if files_payload:
        upload_files_and_start_index(files_payload)

    st.rerun()


def process_pending_upload():
    pending_upload = st.session_state.get("pending_upload")
    pending_upload_sig = st.session_state.get("pending_upload_sig", "")

    if not pending_upload:
        return

    if pending_upload_sig == st.session_state.get("last_uploaded_file_sig", ""):
        st.session_state.pending_upload = None
        st.session_state.pending_upload_sig = ""
        return

    start_upload_process(pending_upload)
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


def render_upload_status_box():
    active_job = st.session_state.get("active_job_detail") or {}
    active_job_id = st.session_state.get("active_job_id")

    if st.session_state.get("uploading"):
        st.info("파일 업로드 중입니다...")

    if st.session_state.get("indexing") and active_job_id:
        project_name = normalize_project_name(
            active_job.get("project_name") or st.session_state.get("latest_project_name")
        )
        progress = calc_job_progress(active_job)
        status = active_job.get("status") or "queued"
        message = active_job.get("message") or ""

        st.markdown(f"**현재 프로젝트:** {project_name}")
        st.progress(progress / 100.0)
        st.caption(f"상태: {status} · 진행률: {progress}%")
        if message:
            st.caption(message)

    if not st.session_state.get("uploading") and not st.session_state.get("indexing"):
        latest_project_name = st.session_state.get("latest_project_name")
        if latest_project_name:
            st.success(f"{latest_project_name} 업로드/인덱싱 작업이 완료되었습니다.")


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
        file_sig = "|".join([f"{uploaded_file.name}:{uploaded_file.size}" for uploaded_file in uploaded_files])

        if file_sig != st.session_state.get("last_uploaded_file_sig", ""):
            st.session_state.uploading = True
            st.session_state.indexing = False
            st.session_state.pending_upload = uploaded_files
            st.session_state.pending_upload_sig = file_sig
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
    elif project_name and project_name != "전체":
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
            f"- 선택 프로젝트: {project_name or '전체'}\n"
            f"- 프로젝트 ID: {project_id or '없음'}\n"
            f"- 원본 오류: {error}\n\n"
            "이 오류가 계속 뜨면 백엔드 /ask와 Ollama 연결 상태를 점검하세요."
        )


def _clear_project_session(pid: str):
    """삭제된 프로젝트를 세션에서 제거하고 전체 보기로 돌아갑니다."""
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
        st.session_state.chat_project_select = "전체"
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
            is_admin = st.session_state.get("admin_role", False)
            btn_label = "🗑 프로젝트 삭제" if is_admin else "🗑 히스토리 삭제"
            confirm_key = f"delete_confirm_{pid}"

            if st.button(btn_label, key="del_action_btn", use_container_width=True):
                # 버튼 클릭 시 confirm 플래그 토글
                st.session_state[confirm_key] = not st.session_state.get(confirm_key, False)
                st.rerun()

    # ── 삭제 확인 다이얼로그 ─────────────────────────────────
    if pid:
        confirm_key = f"delete_confirm_{pid}"
        if st.session_state.get(confirm_key, False):
            is_admin = st.session_state.get("admin_role", False)
            if is_admin:
                st.warning(
                    f"⚠️ **'{pname}'** 프로젝트의 모든 데이터(소스·벡터·히스토리·인덱스)를 삭제합니다.\n\n"
                    "이 작업은 되돌릴 수 없습니다."
                )
            else:
                st.warning(
                    f"⚠️ **'{pname}'** 프로젝트의 대화 히스토리를 삭제합니다."
                )

            c1, c2, _ = st.columns([1, 1, 3])
            with c1:
                if st.button("✅ 확인", key="del_ok_btn", type="primary", use_container_width=True):
                    st.session_state[confirm_key] = False
                    if is_admin:
                        # admin: 프로젝트 전체 데이터 삭제 (SQLite 전 테이블 + Qdrant)
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
                    else:
                        # 일반 사용자: 본인의 히스토리만 삭제
                        try:
                            r = api_delete(
                                f"/history/{pid}",
                                timeout=20,
                            )
                            r.raise_for_status()
                            key = project_key(pid)
                            st.session_state.project_histories.pop(key, None)
                            st.session_state.history_items = []
                            st.success("히스토리가 삭제되었습니다.")
                        except Exception as e:
                            st.error(f"히스토리 삭제 실패: {e}")
                    time.sleep(0.8)
                    st.rerun()
            with c2:
                if st.button("❌ 취소", key="del_cancel_btn", use_container_width=True):
                    st.session_state[confirm_key] = False
                    st.rerun()

    # ── 프로젝트 캡션 ────────────────────────────────────────
    if pid:
        st.caption(f"현재 프로젝트 공간: {project_name_by_id(pid)}")
    else:
        st.info("전체 보기에서는 모든 프로젝트 대화가 표시됩니다.")

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
    jobs = fetch_index_jobs(force=True)
    projects = fetch_projects(force=True)
    job = build_project_job_map(projects, jobs).get(pid)
    locked = not project_selectable(job)

    disabled_reason = None
    if st.session_state.get("uploading"):
        disabled_reason = "업로드 진행 중입니다."
    elif locked:
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
    pid = current_project_id()
    fetch_history(project_id=pid, force=True)
    rebuild_project_histories_from_server()
    refresh_active_job()


def trigger_live_refresh():
    if st.session_state.get("uploading") or st.session_state.get("indexing"):
        st_autorefresh(interval=2000, key="live_job_refresh")


if not st.session_state.get("user_id"):
    render_login_page()
    st.stop()

bootstrap()
process_pending_upload()

st.title("🧠 IT-Smart CodeMind")
st.caption(f"자동 업로드/자동 인덱싱 · 프로젝트 선택형 대화 · 사용자: {st.session_state.user_id}")

with st.sidebar:
    render_system_status()
    st.divider()
    render_sidebar_projects()
    st.divider()
    if st.session_state.admin_role: # admin 인 경우에만 렌더링
        render_reset_box()

render_upload_area()
st.divider()

if render_duplicate_confirm_dialog():
    st.stop()

render_chat_area()
trigger_live_refresh()
