FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 전체 복사 (.streamlit/config.toml 포함)
COPY . .

# .streamlit 디렉토리가 HOME 기준 경로에도 존재해야
# Streamlit 이 config 를 확실히 인식한다
# RUN mkdir -p /root/.streamlit && \
#     cp -r .streamlit/. /root/.streamlit/

EXPOSE 8501

# --server.maxUploadSize : config.toml 과 이중 보장
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.maxUploadSize=1024"]
