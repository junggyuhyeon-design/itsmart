from sentence_transformers import SentenceTransformer
from backend.config import Settings # 경로 수정

class EmbeddingService:
    def __init__(self, settings: Settings):
        self.model = SentenceTransformer(settings.embedding_model)

    @property
    def dimension(self):
        return self.model.get_sentence_embedding_dimension()

    def embed_texts(self, texts: list[str]):
        return self.model.encode(texts).tolist()

    def embed_query(self, query: str):
        return self.embed_texts([query])[0]