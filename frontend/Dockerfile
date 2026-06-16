FROM python:3.10-slim

WORKDIR /app

# 컨텍스트가 루트이므로 경로 명시
COPY frontend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

# 실행 명령은 docker-compose에서 덮어쓰도록 설정함
# CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]