import streamlit as st
import httpx
import os
import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from backend.utils.file_utils import save_uploaded_file, process_uploads_and_collect, ALLOWED_EXTENSIONS
from backend.config import get_settings

# Docker 내부 네트워크 주소 (chatbot-backend 서비스 이름 사용)
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://chatbot-backend:8000")
settings = get_settings()

st.set_page_config(page_title="IT-Smart Source Analyzer", layout="wide")

def init_session():
    if "analysis_targets" not in st.session_state:
        st.session_state.analysis_targets = []
    if "db_analysis" not in st.session_state:
        st.session_state.db_analysis = None

def get_streaming_response(question, extra=""):
    """FastAPI로부터 스트리밍 답변을 받아오는 제너레이터"""
    try:
        with httpx.Client(timeout=300.0) as client:
            with client.stream("GET", f"{FASTAPI_URL}/ask", params={"question": question, "extra_context": extra}) as r:
                for chunk in r.iter_text():
                    yield chunk
    except Exception as e:
        yield f"\n❌ 백엔드 연결 실패: {str(e)}\n(접속 시도 URL: {FASTAPI_URL})"


def main():
    init_session()
    st.title("🚀 IT-Smart Source Analyzer")

    tabs = st.tabs(["업로드 & 인덱싱", "구조 요약", "DB 관계도", "AI 질문하기", "상태 관리"])

    # 1. 업로드 & 인덱싱 탭
    with tabs[0]:
        st.subheader("📁 소스 파일 업로드")
        files = st.file_uploader("ZIP 또는 소스 파일을 선택하세요", accept_multiple_files=True)

        if st.button("파일 저장 및 분석 실행"):
            if not files:
                st.warning("파일을 먼저 선택해주세요.")
            else:
                for f in files:
                    save_uploaded_file(f, settings.upload_dir)
                # ZIP 압축 해제 및 파일 목록 수집
                targets = process_uploads_and_collect(settings.upload_dir)
                st.session_state.analysis_targets = [t.__dict__ for t in targets]
                st.success(f"✅ {len(st.session_state.analysis_targets)}개 파일 수집 완료")

        if st.session_state.analysis_targets:
            st.info(f"현재 수집된 파일: {len(st.session_state.analysis_targets)}개")
            if st.button("🚀 전체 인덱싱 시작 (Qdrant 저장)", type="primary"):
                with st.spinner("백엔드에서 인덱싱 중..."):
                    try:
                        resp = httpx.post(f"{FASTAPI_URL}/index", json=st.session_state.analysis_targets, timeout=None)
                        st.success(f"✅ 인덱싱 완료! 생성된 청크: {resp.json().get('total_chunks')}")
                    except Exception as e:
                        st.error(f"백엔드 통신 에러: {e}")

    # 2. 구조 요약 탭
    with tabs[1]:
        st.subheader("📊 프로젝트 구조")
        if st.button("구조 분석 실행"):
            if not st.session_state.analysis_targets:
                st.warning("먼저 파일을 업로드해주세요.")
            else:
                try:
                    resp = httpx.post(f"{FASTAPI_URL}/summary", json=st.session_state.analysis_targets)
                    data = resp.json()
                    st.code(data.get("tree_str"))
                except Exception as e:
                    st.error(f"백엔드 통신 에러: {e}")

    # 3. DB 관계도 탭
    with tabs[2]:
        st.subheader("🗄️ 소스-DB 관계 분석")
        if st.button("관계 분석 및 Mermaid 생성"):
            if not st.session_state.analysis_targets:
                st.warning("먼저 파일을 업로드해주세요.")
            else:
                try:
                    resp = httpx.post(f"{FASTAPI_URL}/analyze-db", json=st.session_state.analysis_targets)
                    st.session_state.db_analysis = resp.json()
                    st.code(st.session_state.db_analysis.get("mermaid"), language="mermaid")
                    st.info("💡 위 코드를 복사하여 Mermaid Live Editor 등에서 시각화할 수 있습니다.")
                except Exception as e:
                    st.error(f"백엔드 통신 에러: {e}")

    # 4. 질문하기 탭
    with tabs[3]:
        st.subheader("💬 AI 코드 분석 (RAG)")
        query = st.text_input("소스 코드에 대해 궁금한 점을 물어보세요")
        if query:
            extra = ""
            if st.session_state.db_analysis:
                extra = f"\n\n[참고: DB 분석 데이터]\n{st.session_state.db_analysis.get('db_data')}"

            with st.chat_message("assistant"):
                st.write_stream(get_streaming_response(query, extra))

    # 5. 상태 관리 탭
    with tabs[4]:
        st.subheader("⚙️ 시스템 상태")
        if st.button("상태 새로고침"):
            try:
                status = httpx.get(f"{FASTAPI_URL}/status").json()
                st.metric("저장된 청크 수", f"{status.get('chunk_count'):,}")
            except Exception as e:
                st.error(f"백엔드 연결 실패: {e}")

        if st.button("⚠️ 모든 데이터 초기화", type="secondary"):
            try:
                httpx.delete(f"{FASTAPI_URL}/reset")
                st.success("데이터베이스가 초기화되었습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"초기화 실패: {e}")

if __name__ == "__main__":
    main()