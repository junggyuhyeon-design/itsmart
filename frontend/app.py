import os
import re
import html

import httpx
import streamlit as st
import streamlit.components.v1 as components

FASTAPI_URL = os.getenv("FASTAPI_URL", "http://codeMind-backend:8000")

st.set_page_config(page_title="IT-Smart Source Analyzer", layout="wide")


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


def init_session():
    if "analysis_targets" not in st.session_state:
        st.session_state.analysis_targets = []
    if "db_analysis" not in st.session_state:
        st.session_state.db_analysis = None
    if "system_status" not in st.session_state:
        st.session_state.system_status = None


def get_streaming_response(question, extra=""):
    try:
        with httpx.Client(timeout=300.0) as client:
            with client.stream(
                    "GET",
                    f"{FASTAPI_URL}/ask",
                    params={"question": question, "extra_context": extra},
            ) as r:
                for chunk in r.iter_text():
                    yield chunk
    except Exception as e:
        yield f"\n❌ 백엔드 연결 실패: {str(e)}\n(접속 시도 URL: {FASTAPI_URL})"


def render_mermaid(mermaid_code: str, height: int = 900):
    if not mermaid_code or not mermaid_code.strip():
        st.warning("렌더링할 Mermaid 코드가 없습니다.")
        return

    escaped_code = html.escape(mermaid_code)

    components.html(
        f"""
        <div style="width:100%; overflow:auto; border:1px solid #e5e7eb; border-radius:12px; padding:16px; background:#ffffff;">
            <pre class="mermaid" style="text-align:center;">{escaped_code}</pre>
        </div>

        <script type="module">
            import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";

            mermaid.initialize({{
                startOnLoad: true,
                theme: "default",
                securityLevel: "loose",
                flowchart: {{
                    useMaxWidth: true,
                    htmlLabels: true,
                    curve: "basis"
                }}
            }});
        </script>
        """,
        height=height,
        scrolling=True,
    )


def extract_mermaid_blocks(text: str):
    if not text:
        return []

    patterns = [
        r"```mermaid\s*(.*?)```",
        r"```[\r\n]+(graph\s+(?:TD|LR|RL|BT).*?)```",
        r"```[\r\n]+(flowchart\s+(?:TD|LR|RL|BT).*?)```",
    ]

    blocks = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.I | re.S)
        for match in matches:
            code = match.strip()
            if code and code not in blocks:
                blocks.append(code)

    if not blocks:
        direct_match = re.search(r"((?:graph|flowchart)\s+(?:TD|LR|RL|BT)\b.*)", text, re.I | re.S)
        if direct_match:
            blocks.append(direct_match.group(1).strip())

    return blocks


def status_label(status: str) -> str:
    labels = {
        "running": "🟢 실행 중",
        "available": "🟢 사용 가능",
        "loaded": "🟢 로드됨",
        "stopped": "🔴 중지됨",
        "missing": "🔴 없음",
        "not_loaded": "🟡 미로드",
        "error": "🟠 오류",
        "healthy": "🟢 정상",
        "degraded": "🟠 일부 문제",
    }
    return labels.get(status, f"⚪ {status}")


def fetch_system_status():
    resp = httpx.get(f"{FASTAPI_URL}/status", timeout=30.0)
    resp.raise_for_status()
    return resp.json()


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


def main():
    init_session()
    st.title("🚀 IT-Smart Source Analyzer")

    tabs = st.tabs(["업로드 & 인덱싱", "구조 요약", "DB 관계도", "AI 질문하기", "상태 관리"])

    with tabs[0]:
        st.subheader("📁 소스 파일 업로드")
        files = st.file_uploader("ZIP 또는 소스 파일을 선택하세요", accept_multiple_files=True)

        if st.button("파일 저장 및 분석 실행"):
            if not files:
                st.warning("파일을 먼저 선택해주세요.")
            else:
                with st.spinner("백엔드로 파일 전송 중..."):
                    try:
                        upload_files = [
                            (f.name, f.getvalue(), "application/octet-stream") for f in files
                        ]
                        resp = httpx.post(
                            f"{FASTAPI_URL}/upload",
                            files=[("files", uf) for uf in upload_files],
                            timeout=300.0,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        st.session_state.analysis_targets = data.get("targets", [])
                        st.success(f"✅ {data.get('count', 0)}개 파일 수집 완료 (백엔드 저장)")
                    except Exception as e:
                        st.error(f"백엔드 통신 에러: {e}")

        if st.session_state.analysis_targets:
            st.info(f"현재 수집된 파일: {len(st.session_state.analysis_targets)}개")

            if st.button("🚀 전체 인덱싱 시작 (Qdrant 저장)", type="primary"):
                with st.spinner("백엔드에서 인덱싱 중..."):
                    try:
                        resp = httpx.post(
                            f"{FASTAPI_URL}/index",
                            json=st.session_state.analysis_targets,
                            timeout=None,
                        )
                        if resp.is_success:
                            data = resp.json()
                            total_chunks = data.get("total_chunks") or 0
                            st.success(f"✅ 인덱싱 완료! 생성된 청크: {format_count(total_chunks)}")
                            if data.get("logs"):
                                with st.expander("인덱싱 로그"):
                                    st.text("\n".join(data["logs"]))
                        else:
                            st.error(f"인덱싱 실패: {api_error_message(resp)}")
                    except Exception as e:
                        st.error(f"백엔드 통신 에러: {e}")

    with tabs[1]:
        st.subheader("📊 프로젝트 구조")

        if st.button("구조 분석 실행"):
            if not st.session_state.analysis_targets:
                st.warning("먼저 파일을 업로드해주세요.")
            else:
                try:
                    resp = httpx.post(
                        f"{FASTAPI_URL}/summary",
                        json=st.session_state.analysis_targets,
                        timeout=120.0,
                    )
                    data = resp.json()
                    st.code(data.get("tree_str"))
                except Exception as e:
                    st.error(f"백엔드 통신 에러: {e}")

    with tabs[2]:
        st.subheader("🗄️ 소스-DB 관계 분석")

        if st.button("관계 분석 및 Mermaid 생성"):
            if not st.session_state.analysis_targets:
                st.warning("먼저 파일을 업로드해주세요.")
            else:
                with st.spinner("DDL 파싱 및 소스-테이블 관계 분석 중..."):
                    try:
                        resp = httpx.post(
                            f"{FASTAPI_URL}/analyze-db",
                            json=st.session_state.analysis_targets,
                            timeout=300.0,
                        )
                        resp.raise_for_status()
                        st.session_state.db_analysis = resp.json()
                    except Exception as e:
                        st.error(f"백엔드 통신 에러: {e}")

        if st.session_state.db_analysis:
            db_data = st.session_state.db_analysis.get("db_data", {})
            tables = db_data.get("tables", [])
            table_details = db_data.get("table_details", [])
            relations = db_data.get("relations", [])

            st.markdown(f"### 📋 DDL 테이블 목록 ({len(tables)}개)")
            if table_details:
                table_rows = []
                for detail in table_details:
                    columns = detail.get("columns", [])
                    table_rows.append({
                        "테이블명": detail.get("table_name", ""),
                        "컬럼 수": detail.get("column_count", len(columns)),
                        "컬럼": ", ".join(columns) if columns else "-",
                        "DDL 파일": detail.get("source_file", ""),
                    })
                st.dataframe(table_rows, use_container_width=True, hide_index=True)
            elif tables:
                table_defs = db_data.get("table_definitions", {})
                table_rows = [
                    {
                        "테이블명": name,
                        "DDL 파일": table_defs.get(name, "-"),
                    }
                    for name in tables
                ]
                st.dataframe(table_rows, use_container_width=True, hide_index=True)
            else:
                st.warning("DDL SQL 파일에서 CREATE TABLE 정의를 찾지 못했습니다. .sql 파일을 함께 업로드해주세요.")

            st.markdown(f"### 🔗 소스-테이블 관계 ({len(relations)}건)")
            if relations:
                relation_rows = []
                for rel in relations:
                    relation_rows.append({
                        "파일": rel.get("file_name", rel.get("file", "")),
                        "엔티티 유형": rel.get("entity_type", ""),
                        "엔티티명": rel.get("entity_name", ""),
                        "테이블": rel.get("table", ""),
                        "작업": ", ".join(rel.get("operations", [])),
                        "분류": ", ".join(rel.get("categories", [])),
                    })
                st.dataframe(relation_rows, use_container_width=True, hide_index=True)
            else:
                st.info("소스 코드에서 DDL 테이블 참조를 찾지 못했습니다.")

            mermaid_code = st.session_state.db_analysis.get("mermaid", "")
            if mermaid_code.strip():
                st.markdown("### 📊 Mermaid 관계도")
                render_mermaid(mermaid_code, height=1000)

                with st.expander("Mermaid 원본 코드 보기"):
                    st.code(mermaid_code, language="mermaid")
            else:
                st.warning("표시할 Mermaid 다이어그램이 없습니다.")

    with tabs[3]:
        st.subheader("💬 AI 코드 분석 (RAG)")
        query = st.text_input("소스 코드에 대해 궁금한 점을 물어보세요")

        if query:
            extra = ""
            if st.session_state.db_analysis:
                extra = f"\n\n[참고: DB 분석 데이터]\n{st.session_state.db_analysis.get('db_data')}"

            with st.chat_message("assistant"):
                full_text = st.write_stream(get_streaming_response(query, extra))

            if isinstance(full_text, str):
                mermaid_blocks = extract_mermaid_blocks(full_text)
                if mermaid_blocks:
                    st.markdown("### Mermaid 다이어그램")
                    for idx, block in enumerate(mermaid_blocks, start=1):
                        st.markdown(f"#### 다이어그램 {idx}")
                        render_mermaid(block, height=900)

    with tabs[4]:
        st.subheader("⚙️ 시스템 상태")
        st.caption("필요한 Docker 컨테이너와 LLM/임베딩 모델의 연결 상태를 확인합니다.")

        refresh_col, _ = st.columns([1, 4])
        with refresh_col:
            refresh_clicked = st.button("🔄 상태 새로고침", type="primary", use_container_width=True)

        if refresh_clicked or st.session_state.system_status is None:
            with st.spinner("상태 확인 중..."):
                try:
                    st.session_state.system_status = fetch_system_status()
                except Exception as e:
                    st.session_state.system_status = None
                    st.error(f"백엔드 연결 실패: {e}")

        if st.session_state.get("system_status"):
            render_status_panel(st.session_state.system_status)

        st.divider()

        if st.button("⚠️ 모든 데이터 초기화", type="secondary"):
            try:
                httpx.delete(f"{FASTAPI_URL}/reset")
                st.success("데이터베이스가 초기화되었습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"초기화 실패: {e}")


if __name__ == "__main__":
    main()