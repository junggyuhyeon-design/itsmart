from sentence_transformers import SentenceTransformer

from config import Settings


class EmbeddingService:
    def __init__(self, settings: Settings) -> None:
        self.model = SentenceTransformer(settings.embedding_model)

    @property
<<<<<<< HEAD
    def dimension(self):
        """임베딩 모델(BAAI/bge-m3) 자체가 정의한 벡터 크기 반환"""
        return self.model.get_embedding_dimension()

    def embed_texts(self, texts: list[str]):
        """임베딩 모델(BAAI/bge-m3)이 벡터화를 진행"""
        return self.model.encode(texts).tolist()

    def embed_query(self, query: str):
        """단일 문자열(질의)을 임베딩 벡터로 변환"""
=======
    def dimension(self) -> int:
        return self.model.get_sentence_embedding_dimension()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts).tolist()

    def embed_query(self, query: str) -> list[float]:
>>>>>>> e3e85489126674750763f7592c68a889f1fce4c9
        return self.embed_texts([query])[0]