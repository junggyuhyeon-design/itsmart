import os
import re
import time
from datetime import datetime
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

BACKEND_URL = os.getenv("FASTAPI_URL", "http://codeMind-backend:8000")

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
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


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

    unique_by_name: dict[str, dict] = {}
    for project in sorted(by_project_id.values(), key=lambda item: item.get("uploaded_at") or "", reverse=True):
        project_name = normalize_project_name(project.get("project_name"))
        if project_name not in unique_by_name:
            unique_by_name[project_name] = project

    return list(unique_by_name.values())


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

        valid_names = {"전체"} | {
            normalize_project_name(project.get("project_name"))
            for project in st.session_state.projects
        }
        if st.session_state.chat_project_select not in valid_names:
            st.session_state.chat_project_select = "전체"

    except Exception as error:
        st.session_state.projects = []
        st.session_state.projects_error = str(error)

    return st.session_state.projects


def fetch_index_jobs(force: bool = False):
    if st.session_state.index_jobs and not force:
        return st.session_state.index_jobs

    last_error = None

    for path in ("/index/jobs", "/index-jobs"):
        try:
            response = api_get(path, params={"limit": 50}, timeout=20)
            response.raise_for_status()
            data = response.json()
            st.session_state.index_jobs = data.get("jobs", [])
            st.session_state.index_job_error = None
            return st.session_state.index_jobs
        except Exception as error:
            last_error = error

    st.session_state.index_jobs = []
    st.session_state.index_job_error = str(last_error) if last_error else "index job fetch failed"
    return st.session_state.index_jobs


def fetch_index_job_detail(job_id: str):
    for path in (f"/index/jobs/{job_id}", f"/index-jobs/{job_id}"):
        try:
            response = api_get(path, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception:
            pass
    return None


def fetch_history(force: bool = False):
    if st.session_state.history_items and not force:
        return st.session_state.history_items

    try:
        response = api_get("/history", params={"limit": 300}, timeout=20)
        response.raise_for_status()
        data = response.json()
        st.session_state.history_items = data.get("history", [])
        st.session_state.history_error = None
    except Exception as error:
        st.session_state.history_items = []
        st.session_state.history_error = str(error)

    return st.session_state.history_items


def rebuild_project_histories_from_server():
    histories = st.session_state.get("history_items") or []
    project_histories: dict[str, list[dict]] = {}

    ordered = list(reversed(histories))

    for item in ordered:
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        created_at = item.get("created_at")
        ts = parse_created_at_to_ts(created_at)

        project_name = "전체"
        stripped_question = question

        if question.startswith("[") and "]" in question:
            try:
                project_name = question[1:question.index("]")].strip() or "전체"
                stripped_question = question[question.index("]") + 1:].strip()
            except Exception:
                project_name = "전체"
                stripped_question = question

        if project_name not in project_histories:
            project_histories[project_name] = []

        if stripped_question:
            project_histories[project_name].append(
                {"role": "user", "content": stripped_question, "ts": ts}
            )

        if answer:
            project_histories[project_name].append(
                {"role": "assistant", "content": answer, "ts": ts}
            )

    st.session_state.project_histories = project_histories


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
    result = {}

    for project in projects:
        project_name = normalize_project_name(project.get("project_name"))
        matched = [job for job in jobs if normalize_project_name(job.get("project_name")) == project_name]
        if not matched:
            continue

        matched.sort(
            key=lambda item: (
                item.get("updated_at") or "",
                item.get("created_at") or "",
            ),
            reverse=True,
        )
        result[project_name] = matched[0]

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
        st.rerun()

    current = current_project_name()

    for project in projects:
        project_name = normalize_project_name(project.get("project_name"))
        project_id = (project.get("project_id") or "").strip() or project_name
        job = project_job_map.get(project_name)

        disabled = not project_selectable(job)
        status_label = get_project_status_label(job)
        progress = calc_job_progress(job) if job else None
        selected = current == project_name

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


def upload_files_and_start_index(uploaded_files):
    if not uploaded_files:
        return

    st.session_state.uploading = True
    st.session_state.indexing = False
    st.session_state.active_job_id = None
    st.session_state.active_job_detail = None

    files_payload = []
    file_sig_parts = []

    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.getvalue()
        file_sig_parts.append(f"{uploaded_file.name}:{len(file_bytes)}")
        files_payload.append(
            ("files", (uploaded_file.name, file_bytes, uploaded_file.type or "application/octet-stream"))
        )

    st.session_state.last_uploaded_file_sig = "|".join(file_sig_parts)

    try:
        upload_response = api_post("/upload", files=files_payload, timeout=300)
        upload_response.raise_for_status()
        upload_data = upload_response.json()

        st.session_state.last_upload_result = upload_data
        targets = upload_data.get("targets", [])
        st.session_state.last_uploaded_targets = targets
        st.session_state.uploading = False

        if not targets:
            st.error("업로드는 완료됐지만 인덱싱 대상이 없습니다.")
            return

        project_name = normalize_project_name(targets[0].get("project_name"))
        st.session_state.latest_project_name = project_name

        job_created = False
        last_error = None

        for path in ("/index/jobs", "/index-jobs"):
            try:
                index_response = api_post(path, json_data={"targets": targets}, timeout=60)
                index_response.raise_for_status()
                job_data = index_response.json()
                st.session_state.active_job_id = job_data.get("job_id")
                st.session_state.indexing = True
                job_created = True
                break
            except Exception as error:
                last_error = error

        if not job_created:
            raise last_error if last_error else RuntimeError("index job create failed")

        fetch_projects(force=True)
        fetch_index_jobs(force=True)

    except Exception as error:
        st.session_state.uploading = False
        st.session_state.indexing = False
        st.session_state.active_job_id = None
        st.session_state.active_job_detail = None
        st.error(f"업로드/인덱싱 시작 실패: {error}")


def process_pending_upload():
    pending_upload = st.session_state.get("pending_upload")
    pending_upload_sig = st.session_state.get("pending_upload_sig", "")

    if not pending_upload:
        return

    if pending_upload_sig == st.session_state.get("last_uploaded_file_sig", ""):
        st.session_state.pending_upload = None
        st.session_state.pending_upload_sig = ""
        return

    upload_files_and_start_index(pending_upload)
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


def trigger_live_refresh():
    if st.session_state.get("uploading") or st.session_state.get("indexing"):
        st_autorefresh(interval=2000, key="live_job_refresh")


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
        with api_get("/ask", params=params, timeout=300, stream=True) as response:
            if response.status_code < 400:
                for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                    if chunk:
                        chunks.append(chunk)

                answer = "".join(chunks).strip()
                if answer:
                    return answer

            elif response.status_code != 405:
                return f"백엔드 /ask 오류: HTTP {response.status_code} - {response.text}"
    except Exception:
        pass

    payload = {
        "question": question,
        "top_k": 5,
        "extra_context": "",
    }

    if project_name and project_name != "전체":
        payload["project_name"] = project_name

    chunks = []

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
            f"- 원본 오류: {error}\n\n"
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
    for message in visible_messages:
        with st.chat_message("user" if message["role"] == "user" else "assistant"):
            if message["role"] == "assistant":
                render_answer(message["content"])
            else:
                st.markdown(message["content"])

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


def bootstrap():
    fetch_system_status(force=True)
    fetch_projects(force=True)
    fetch_index_jobs(force=True)
    fetch_history(force=True)
    rebuild_project_histories_from_server()
    refresh_active_job()


bootstrap()
process_pending_upload()

st.title("🧠 IT-Smart CodeMind")
st.caption("자동 업로드/자동 인덱싱 · 프로젝트 선택형 대화")

with st.sidebar:
    render_system_status()
    st.divider()
    render_sidebar_projects()
    st.divider()
    render_reset_box()

render_upload_area()
st.divider()
render_chat_area()

trigger_live_refresh()