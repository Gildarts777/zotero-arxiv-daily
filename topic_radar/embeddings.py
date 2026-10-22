"""Paper encoders: any sentence-transformers model, or a raw HF model with CLS pooling (+ optional adapter).

SPECTER2 needs the second path: `allenai/specter2_base` + the `allenai/specter2` proximity adapter,
input "title [SEP] abstract", CLS pooling (see the model card).
"""

import numpy as np


class Encoder:
    def __init__(self, cfg):
        self.model_name = cfg["model"]
        self.adapter = cfg.get("adapter")
        self.pooling = cfg.get("pooling", "mean")
        self.batch_size = cfg.get("batch_size", 32)
        if self.adapter or self.pooling == "cls":
            self._init_hf()
        else:
            from sentence_transformers import SentenceTransformer
            self.st = SentenceTransformer(self.model_name)

    def _init_hf(self):
        import torch
        from transformers import AutoTokenizer
        self.torch = torch
        self.st = None
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        if self.adapter:
            from adapters import AutoAdapterModel
            self.model = AutoAdapterModel.from_pretrained(self.model_name)
            self.model.load_adapter(self.adapter, source="hf", load_as="proximity", set_active=True)
        else:
            from transformers import AutoModel
            self.model = AutoModel.from_pretrained(self.model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device).eval()

    def encode(self, papers):
        """papers: list of (title, abstract). Returns L2-normalised embeddings (n, d)."""
        if self.st is not None:
            texts = [f"{t}. {a}".strip() if a else t for t, a in papers]
            return np.asarray(self.st.encode(texts, normalize_embeddings=True,
                                             batch_size=self.batch_size, show_progress_bar=False))
        sep = self.tokenizer.sep_token
        texts = [f"{t}{sep}{a}" if a else t for t, a in papers]
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), self.batch_size):
                batch = self.tokenizer(texts[i:i + self.batch_size], padding=True, truncation=True,
                                       max_length=512, return_tensors="pt", return_token_type_ids=False)
                hidden = self.model(**batch.to(self.device)).last_hidden_state
                out.append(hidden[:, 0, :].float().cpu().numpy())
        emb = np.concatenate(out) if out else np.zeros((0, self.model.config.hidden_size))
        return emb / np.linalg.norm(emb, axis=1, keepdims=True).clip(min=1e-12)
