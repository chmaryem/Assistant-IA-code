"""
config.py — Configuration globale du système CodeAudit AI
==========================================================
Changements vs l'original :

RAGConfig :
  • embedding_model → jinaai/jina-embeddings-v2-base-code
    (modèle spécialisé code, dimension 768 vs 384 pour all-MiniLM)
  • embedding_dimension : 768 (nécessaire pour Chroma si on recrée la collection)
  • relevance_threshold : 0.75 (seuil de similarité — docs en-dessous ignorés)
  • top_k : 8 → on élargit pour compenser le filtre de seuil
  • chunk_size : 800 (plus petit pour des chunks code plus précis)
  • chunk_overlap : 150

RAGConfig.use_gpu : auto-détecté (CUDA > MPS > CPU)

WatcherConfig :
  • debounce_seconds : 4.0 (corrige la triple-analyse IntelliJ/VSCode)

APIConfig :
  • temperature : 0.0 (analyses déterministes, plus de contradictions entre runs)
"""

import os
import logging
from pathlib import Path
from typing import List

from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Détection du device optimal pour les embeddings
# ─────────────────────────────────────────────────────────────────────────────

def _detect_optimal_device() -> str:
    """
    Détecte le meilleur device disponible dans l'ordre : CUDA > MPS > CPU.
    Jina v2 tourne ~8x plus vite sur GPU qu'en CPU pour des fichiers de code.
    """
    try:
        import torch
        if torch.cuda.is_available():
            logger.info("Embeddings device: CUDA (GPU NVIDIA détecté)")
            return "cuda"
        if torch.backends.mps.is_available():
            logger.info("Embeddings device: MPS (Apple Silicon détecté)")
            return "mps"
    except ImportError:
        pass  # torch non installé → CPU
    logger.info("Embeddings device: CPU")
    return "cpu"


# ─────────────────────────────────────────────────────────────────────────────
# Sous-configurations Pydantic
# ─────────────────────────────────────────────────────────────────────────────

class APIConfig(BaseModel):
    """Configuration de l'API LLM (Groq / OpenAI)."""
    provider:    str   = "groq"
    api_key:     str   = os.getenv("API_KEY", "")
    model:       str   = "llama-3.3-70b-versatile"
    temperature: float = 0.0        # 0.0 = déterministe → même résultat sur 3 runs
    max_tokens:  int   = 4096


class RAGConfig(BaseModel):
    """
    Configuration du pipeline RAG.

    Modèle d'embeddings : jinaai/jina-embeddings-v2-base-code
    ──────────────────────────────────────────────────────────
    Ce modèle est entraîné sur du code source (GitHub, StackOverflow, docs API).
    Avantages vs all-MiniLM-L6-v2 pour l'analyse de code :
      • Comprend la sémantique des blocs try/catch, des signatures de méthodes
      • Meilleure distinction SQL injection vs SQL correct
      • Meilleur rappel sur les patterns de sécurité (XSS, CSRF, SSRF)
      • Dimension 768 (vs 384) → espace vectoriel plus expressif

    IMPORTANT — Migration depuis all-MiniLM :
      Si tu as déjà une collection ChromaDB avec all-MiniLM (384 dims),
      tu DOIS supprimer data/vector_store/ avant de redémarrer.
      Les dimensions sont incompatibles et ChromaDB lève une erreur silencieuse.
      → rm -rf data/vector_store/
    """
    embedding_model:     str   = "jinaai/jina-embeddings-v2-base-code"
    embedding_dimension: int   = 768    # Jina v2 = 768 dims (MiniLM = 384)
    embedding_device:    str = None     # "" = auto-détection au runtime
    vector_store:        str   = "chromadb"
    chunk_size:          int   = 800    # Plus petit = chunks plus précis pour le code
    chunk_overlap:       int   = 150
    top_k:               int   = 8      # On élargit car le filtre de seuil va réduire
    relevance_threshold: float = 0.75   # Score cosinus max — au-dessus = non pertinent
    #                                     0.75 = compromis précision/rappel
    #                                     Monter à 0.85 pour plus de précision
    #                                     Descendre à 0.65 pour plus de rappel

    @field_validator("embedding_device", mode="before")
    @classmethod
    def auto_detect_device(cls, v: str) -> str:
        """Auto-détecte le device si non spécifié."""
        return v if v else _detect_optimal_device()

    @model_validator(mode="after")
    def warn_if_wrong_dimension(self) -> "RAGConfig":
        """Avertit si le modèle et la dimension semblent incohérents."""
        model = self.embedding_model.lower()
        dim   = self.embedding_dimension
        known = {
            "jina-embeddings-v2-base-code": 768,
            "jina-embeddings-v2-small-code": 512,
            "all-minilm-l6-v2": 384,
            "all-minilm-l12-v2": 384,
            "text-embedding-ada-002": 1536,
        }
        for key, expected in known.items():
            if key in model and dim != expected:
                logger.warning(
                    "RAGConfig: modèle '%s' a normalement %d dimensions, "
                    "mais embedding_dimension=%d. Vérifiez votre config.",
                    self.embedding_model, expected, dim
                )
        return self


class AnalysisConfig(BaseModel):
    """Configuration de l'analyse de code."""
    supported_languages: List[str] = ["python", "javascript", "typescript", "java"]
    max_file_size_mb:    int        = 5
    max_code_chars:      int        = 10_000   # Budget token pour le code (priorité #1)
    max_knowledge_chars: int        = 2_000    # Budget token pour le contexte RAG
    max_context_chars:   int        = 1_500    # Budget token pour le contexte projet
    exclude_patterns:    List[str]  = [
        "**/node_modules/**",
        "**/__pycache__/**",
        "**/venv/**",
        "**/dist/**",
        "**/build/**",
        "**/.git/**",
        "**/target/**",
    ]
    analysis_depth: str = "medium"


class WatcherConfig(BaseModel):
    """Configuration du file watcher."""
    enabled:             bool       = True
    debounce_seconds:    float      = 4.0    # 4s évite la triple-analyse IntelliJ/VSCode
    analyze_impacted:    bool       = True
    max_impacted_files:  int        = 5
    watched_extensions:  List[str]  = [".py", ".js", ".jsx", ".ts", ".tsx", ".java"]
    excluded_dirs:       List[str]  = [
        "node_modules", "__pycache__", "venv", ".git",
        "dist", "build", ".pytest_cache", ".mypy_cache",
        ".vscode", ".idea", "target", "out",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Configuration globale
# ─────────────────────────────────────────────────────────────────────────────

class Config:
    """
    Configuration globale du projet.

    Arborescence des données :
      data/
        knowledge_base/     ← fichiers .md/.txt de règles (source of truth)
          java/
            security/       ← sql_injection.md, password_and_resources.md
            patterns/       ← solid_encapsulation.md
            performance/    ← pagination_and_threading.md
            quality/        ← clean_code.md
          python/
            security/       ← vulnerabilities.md
            patterns/       ← modern_python.md
            performance/    ← optimization.md
          typescript/
            security/       ← type_safety_and_xss.md
            patterns/       ← async_and_react.md
            performance/    ← react_optimization.md
          general/
            architecture/   ← patterns.md
            testing/
        vector_store/       ← ChromaDB persistée (générée par knowledge_loader.py)
        cache/              ← cache d'analyse par SHA256
    """

    # ── Chemins ───────────────────────────────────────────────────────────────
    BASE_DIR           = Path(__file__).parent
    DATA_DIR           = BASE_DIR / "data"
    KNOWLEDGE_BASE_DIR = DATA_DIR / "knowledge_base"
    VECTOR_STORE_DIR   = DATA_DIR / "vector_store"
    CACHE_DIR          = DATA_DIR / "cache"

    # Création automatique des dossiers
    for _d in [DATA_DIR, KNOWLEDGE_BASE_DIR, VECTOR_STORE_DIR, CACHE_DIR]:
        _d.mkdir(parents=True, exist_ok=True)

    # ── Sous-configurations ───────────────────────────────────────────────────
    api      = APIConfig()
    rag      = RAGConfig()
    analysis = AnalysisConfig()
    watcher  = WatcherConfig()

    # ── Serveur ───────────────────────────────────────────────────────────────
    HOST  = os.getenv("SERVER_HOST", "127.0.0.1")
    PORT  = int(os.getenv("SERVER_PORT", "8000"))
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"

    # ── API ───────────────────────────────────────────────────────────────────
    API_PREFIX = "/api/v1"

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # ── Nom de la collection ChromaDB ─────────────────────────────────────────
    # Inclure le nom du modèle pour éviter les conflits de dimension
    # Si tu changes de modèle → change ce nom pour forcer une nouvelle collection
    CHROMA_COLLECTION = "code_kb_jina_v2"


# Instance globale
config = Config()