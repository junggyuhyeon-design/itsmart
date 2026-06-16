FROM python:3.10-slim

# 헬스체크를 위한 curl 설치
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 컨텍스트가 루트이므로 경로 명시
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# uvicorn 실행 시 경로 주의 (docker-compose.yml에서 command로 덮어씀)
# CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]