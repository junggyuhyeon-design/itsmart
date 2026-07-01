from sentence_transformers import SentenceTransformer
from config import Settings # 경로 수정

class EmbeddingService:
    def __init__(self, settings: Settings):
        self.model = SentenceTransformer(settings.embedding_model)

    @property
    def dimension(self):
        """임베딩 모델(BAAI/bge-m3) 자체가 정의한 벡터 크기 반환"""
        return self.model.get_embedding_dimension()

    def embed_texts(self, texts: list[str]):
        """임베딩 모델(BAAI/bge-m3)이 벡터화를 진행"""
        return self.model.encode(texts).tolist()

    def embed_query(self, query: str):
        """단일 문자열(질의)을 임베딩 벡터로 변환"""
        return self.embed_texts([query])[0]