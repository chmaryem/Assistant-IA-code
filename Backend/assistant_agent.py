from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import config

load_dotenv()
logger = logging.getLogger(__name__)


# Présence d'un de ces patterns → mode sécurité activé dans le prompt
_SECURITY_PATTERNS: dict[str, list[str]] = {
    "java": [
        "Statement", "executeQuery", "executeUpdate",
        "password", "Password", "authenticate", "login",
        "ResultSet", "DriverManager",
    ],
    "python": [
        "cursor.execute", "cursor.executemany",
        "password", "secret", "pickle.loads",
        "subprocess", "eval(", "exec(",
    ],
    "typescript": [
        "innerHTML", "dangerouslySetInnerHTML",
        "localStorage", "eval(", "password",
        "jwt", "token", "secret",
    ],
    "javascript": [
        "innerHTML", "eval(", "password",
        "localStorage", "require('child_process')",
    ],
}


def _has_security_patterns(code: str, language: str) -> bool:
    """Retourne True si le code contient des patterns à risque de sécurité."""
    patterns = _SECURITY_PATTERNS.get(language.lower(), [])
    return any(p in code for p in patterns)


class CodeRAGSystemAPI:


    def __init__(self) -> None:
        self.embeddings:   HuggingFaceEmbeddings | None = None
        self.vector_store: Chroma                | None = None
        self.llm:          ChatGroq              | None = None
        self._initialize()

    def _initialize(self) -> None:
     
        logger.info("Initialisation CodeRAGSystemAPI...")

        #1. Embeddings Jina v2 
        logger.info("Chargement embeddings : %s (device=%s)",
                    config.rag.embedding_model, config.rag.embedding_device)
        self.embeddings = HuggingFaceEmbeddings(
            model_name   = config.rag.embedding_model,
            model_kwargs = {
                "device":            config.rag.embedding_device,
                "trust_remote_code": True,   # Requis pour jinaai/jina-embeddings-v2-base-code
            },
            encode_kwargs = {
                "normalize_embeddings": True,   # Indispensable pour cosine similarity correcte
                "batch_size":           32,
            },
        )

        # 2. ChromaDB
        self.vector_store = Chroma(
            persist_directory  = str(config.VECTOR_STORE_DIR),
            embedding_function = self.embeddings,
            collection_name    = config.CHROMA_COLLECTION,
        )

        chunk_count = self.vector_store._collection.count()
        if chunk_count == 0:
            logger.warning(
                "Collection ChromaDB '%s' est vide ! "
                "Lancez : python knowledge_loader.py",
                config.CHROMA_COLLECTION,
            )
        else:
            logger.info("Collection '%s' : %d chunks disponibles",
                        config.CHROMA_COLLECTION, chunk_count)

        #  3. LLM Groq 
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            logger.warning("GROQ_API_KEY non défini — les analyses LLM échoueront")

        self.llm = ChatGroq(
            model        = config.api.model,
            temperature  = config.api.temperature,  
            api_key      = api_key or "placeholder",
            max_tokens   = config.api.max_tokens,
        )

        logger.info("CodeRAGSystemAPI initialisé ")

    # Retrieval RAG filtré

    def _retrieve_relevant_knowledge(
        self,
        query: str,
        language: str,
        k: int | None = None,
    ) -> tuple[list[Document], list[float]]:
        """
        Pourquoi similarity_search_with_score et non similarity_search ?
          similarity_search ne retourne pas les scores → impossible de filtrer.
          ChromaDB retourne des distances L2 converties en scores cosinus [0, 1].
          Score = 0.0 → documents identiques.
          Score = 1.0 → documents orthogonaux (non pertinents).
          Notre seuil : 0.75 — au-dessus = hors sujet.
        """
        search_k     = (k or config.rag.top_k) * 2 
        threshold    = config.rag.relevance_threshold

        # Recherche avec scores
        results_with_scores = self.vector_store.similarity_search_with_score(
            query, k=search_k
        )

        if not results_with_scores:
            logger.debug("Aucun résultat de recherche pour le code")
            return [], []

        # Filtre 1 : seuil de pertinence 
        filtered = [
            (doc, score)
            for doc, score in results_with_scores
            if score <= threshold
        ]

        n_before = len(results_with_scores)
        n_after  = len(filtered)
        if n_before != n_after:
            logger.debug(
                "RAG filtrage : %d/%d docs conservés (seuil=%.2f)",
                n_after, n_before, threshold
            )

        # Filtre 2 : boost langage (tri stable)
        # Documents du même langage → priorité dans le prompt
        lang_lower = language.lower()
        filtered.sort(
            key=lambda pair: (
                0 if pair[0].metadata.get("language", "") == lang_lower else 1,
                pair[1],   # À égalité de priorité, meilleur score en premier
            )
        )

        # Limite finale 
        final_k  = k or config.rag.top_k
        filtered = filtered[:final_k]

        docs   = [doc   for doc, _ in filtered]
        scores = [score for _, score  in filtered]

        # Log de traçabilité (visible en mode DEBUG)
        for doc, score in zip(docs, scores):
            logger.debug(
                "  [%.3f] %s (%s/%s)",
                score,
                doc.metadata.get("source_file", "?"),
                doc.metadata.get("language",    "?"),
                doc.metadata.get("category",    "?"),
            )

        return docs, scores

    def _build_knowledge_context(
        self,
        docs: list[Document],
        scores: list[float],
    ) -> str:
        """
        Formate les documents RAG pour l'injection dans le prompt.

        Format choisi pour maximiser la compréhension du LLM :
          [Source: sql_injection.md | java/security | score: 0.42]
          <contenu du chunk>

          [Source: password_and_resources.md | java/security | score: 0.58]
          <contenu du chunk>

        Le LLM sait ainsi d'où vient chaque règle et peut y faire référence.
        """
        if not docs:
            return ""

        parts: list[str] = []
        total_chars      = 0
        max_chars        = config.analysis.max_knowledge_chars

        for doc, score in zip(docs, scores):
            meta    = doc.metadata
            source  = meta.get("source_file", "unknown")
            lang    = meta.get("language",    "general")
            cat     = meta.get("category",    "general")
            sev     = meta.get("severity",    "")
            sev_tag = f" | severity: {sev}" if sev else ""

            header  = f"[Source: {source} | {lang}/{cat}{sev_tag} | score: {score:.2f}]"
            content = doc.page_content.strip()
            block   = f"{header}\n{content}"

            if total_chars + len(block) > max_chars:
              
                remaining = max_chars - total_chars
                if remaining > 200:
                    parts.append(block[:remaining] + "\n... [tronqué]")
                break

            parts.append(block)
            total_chars += len(block) + 2   # +2 pour le \n\n de séparation

        return "\n\n".join(parts)

    #Construction du prompt

    def _build_security_section(self, code: str, language: str) -> str:
        if not _has_security_patterns(code, language):
           return ""

    # Détecter toutes les méthodes contenant des patterns dangereux
        import re
        method_pattern = re.compile(
             r'(public|private|protected)\s+\w[\w<>]*\s+(\w+)\s*\([^)]*\)',
             re.MULTILINE
    )
        methods = [m.group(2) for m in method_pattern.finditer(code)]

        return f"""
         SECURITY SCAN MODE — EXHAUSTIVE ANALYSIS REQUIRED

You MUST scan these {len(methods)} methods individually: {', '.join(methods)}

For EACH method that contains a security issue, create a SEPARATE ---FIX START--- block.
Do NOT group multiple methods into one fix block.

Checklist per method:
- SQL string concatenation with + or f-string → CRITICAL (separate block per method)
- password stored/compared in plain text → CRITICAL
- ResultSet/Statement not in try-with-resources → HIGH  
- Exception swallowed (return false / return null in catch) → HIGH
- e.printStackTrace() → MEDIUM

If 4 methods have SQL injection → output 4 separate CRITICAL fix blocks.
"""


    def _build_prompt(
        self,
        code: str,
        context: dict[str, Any],
        knowledge_context: str,
    ) -> str:
        """
        Construit le prompt final avec budget token strict et priorité au code.

        Budget token (priorités décroissantes) :
          1. Code source      → max_code_chars    (10 000) — priorité absolue
          2. Context projet   → max_context_chars (1 500)  — architecture/imports
          3. Knowledge RAG    → max_knowledge_chars (2 000) — best practices filtrées
          4. Dependency info  → ~300 chars          — criticité fichier
        """
        #Extraction du contexte
        file_path       = context.get("file_path", "unknown")
        language        = context.get("language",  "unknown")
        criticality     = context.get("criticality_score", 0)
        dependencies    = context.get("dependencies",  [])
        dependents      = context.get("dependents",    [])
        is_entry_point  = context.get("is_entry_point", False)
        change_type     = context.get("change_type",   "unknown")
        lines_changed   = context.get("lines_changed",  0)
        project_context = context.get("project_context", "")

        max_code    = config.analysis.max_code_chars
        max_ctx     = config.analysis.max_context_chars
        code_to_send = code[:max_code]
        if len(code) > max_code:
            code_to_send += f"\n// ... [TRONQUÉ — {len(code) - max_code} chars restants]"

        project_ctx_compressed = project_context[:max_ctx]

        dependency_info = ""
        if dependencies or dependents:
            status = (
                "CRITICAL" if criticality > 5
                else "IMPORTANT" if criticality > 0
                else "ISOLATED"
            )
            dependency_info = (
                f"\nDEPENDENCY CONTEXT:\n"
                f"• Status: {status} ({criticality} files depend on this)\n"
                f"• Entry point: {'Yes' if is_entry_point else 'No'}\n"
                f"• Uses: {len(dependencies)} file(s) | Used by: {len(dependents)} file(s)\n"
                + (f" Breaking changes will affect {criticality} other files!\n"
                   if criticality > 0 else "")
            )

        focus_map = {
            "new_function":       "New function — check logic, edge cases, missing tests",
            "function_signature": f"Signature changed — verify compatibility with {criticality} dependents",
            "logic_change":       "Logic modified — check consistency with existing architecture",
            "import_change":      "Import change — check for unused imports or missing dependencies",
        }
        focus_area = ""
        if change_type in focus_map:
            focus_area = f"\nFOCUS: {focus_map[change_type]}\n"

        # ── Section Security Scan ─────────────────────────────────────────────
        security_section = self._build_security_section(code, language)

        # ── Règles adaptatives (criticité + security mode) ────────────────────
        issue_limit = (
            "Report ALL CRITICAL issues — no limit. For HIGH: all if criticality > 2, else max 3. For MEDIUM: max 2."
            if criticality > 0 or security_section
            else "Maximum 4 issues — prioritize by impact."
        )

      
        prompt = f"""You are a SENIOR code reviewer. Be concise and actionable. Focus ONLY on significant issues.
{project_ctx_compressed}
{dependency_info}{focus_area}{security_section}
CODE TO ANALYZE:
File: {file_path}
Language: {language}
Change: {lines_changed} line(s) modified

```{language}
{code_to_send}
```

BEST PRACTICES FROM KNOWLEDGE BASE:
{knowledge_context if knowledge_context else "(no relevant rules found for this code)"}

RULES:
1. Skip trivial style issues — assume the developer knows basic syntax.
2. Focus on: COMPILATION ERRORS, security vulnerabilities, critical bugs, architecture violations, performance.
3. SYNTAX/IMPORTS: Check imports against 'Existing Internal Packages' in PROJECT CONTEXT. Missing internal import = CRITICAL compilation error.
4. DO NOT suggest creating files/classes that already exist in the project context.
5. {"NO breaking changes (no renaming public methods/classes) — criticality is HIGH." if criticality > 3 else "Breaking changes acceptable with justification."}
6. {issue_limit}
7. Only suggest libraries that are already in the project imports. Never invent dependencies.
8. If code is correct: respond with exactly " Code quality is good, no major issues."

FORMAT (one block per issue):

---FIX START---
**PROBLEM**: [Concise title — one line]
**SEVERITY**: CRITICAL | HIGH | MEDIUM | LOW
**LOCATION**: [Method name or lines X–Y]

**CURRENT CODE**:
```{language}
[Exact vulnerable/problematic code — no pseudo-code]
```

**FIXED CODE**:
```{language}
[Complete, copy-paste ready fix]
```

**WHY**: [1–2 sentences explaining the risk and the fix]
---FIX END---

ANALYZE:"""

        return prompt

    
    def analyze_code_with_rag(
        self,
        code: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Analyse un fichier de code avec RAG filtré et prompt adaptatif.

        Args:
            code    : Code source complet du fichier
            context : Dictionnaire de contexte (file_path, language, criticality, ...)

        Returns:
            {
                "analysis"          : str  — texte de l'analyse LLM
                "relevant_knowledge": list — métadonnées des docs RAG utilisés
                "rag_scores"        : list — scores cosinus des docs (pour debug)
                "docs_used"         : int  — nombre de docs RAG passés au LLM
                "security_mode"     : bool — True si security scan activé
                "code"              : str
                "context"           : dict
            }
        """
        language = context.get("language", "unknown")

        # 1. Retrieval RAG filtré
        relevant_docs, rag_scores = self._retrieve_relevant_knowledge(
            query    = code,
            language = language,
        )

        # 2. Formatage du contexte de connaissance 
        knowledge_context = self._build_knowledge_context(relevant_docs, rag_scores)

        # ── 3. Construction du prompt 
        prompt = self._build_prompt(code, context, knowledge_context)

        # ── 4. Appel LLM 
        try:
            response = self.llm.invoke(prompt)
            analysis = (
                response.content
                if hasattr(response, "content")
                else str(response)
            )
        except Exception as e:
            logger.error("Erreur LLM lors de l'analyse de %s : %s",
                         context.get("file_path", "?"), e)
            analysis = f" Error: {e}\n\nVérifiez votre GROQ_API_KEY."

        return {
            "analysis":           analysis,
            "relevant_knowledge": [doc.metadata for doc in relevant_docs],
            "rag_scores":         rag_scores,
            "docs_used":          len(relevant_docs),
            "security_mode":      bool(self._build_security_section(code, language)),
            "code":               code,
            "context":            context,
        }

    # Plan de refactoring

    def generate_refactoring_plan(self, analysis_results: list[dict]) -> str:
        """
        Génère un plan de refactoring global à partir de toutes les analyses.
        Prend en compte la criticité des fichiers pour prioriser les phases.
        """
        if not analysis_results:
            return (
                "\n══ PLAN DE REFACTORING ══\n\n"
                "AUCUN FICHIER ANALYSÉ\n\n"
                "Vérifiez que le projet contient des fichiers .py, .js, .ts, .java\n"
                "et qu'ils ne sont pas dans des dossiers exclus.\n"
            )

        #  Construire le résumé des analyses 
        summaries: list[str] = []
        total_critical = total_high = total_medium = 0

        for i, result in enumerate(analysis_results, 1):
            ctx          = result.get("context", {})
            file_name    = Path(ctx.get("file_path", f"File_{i}")).name
            criticality  = ctx.get("criticality_score", 0)
            text         = result.get("analysis", "")
            text_upper   = text.upper()

            c_count = text_upper.count("CRITICAL")
            h_count = text_upper.count("HIGH")
            m_count = text_upper.count("MEDIUM")
            total_critical += c_count
            total_high     += h_count
            total_medium   += m_count

            rag_info = ""
            if result.get("docs_used", 0) > 0:
                rag_info = f"\n  RAG docs utilisés: {result['docs_used']}"

            summaries.append(
                f"\nFichier {i}: {file_name}\n"
                f"  Criticité: {criticality} | CRITICAL: {c_count} | HIGH: {h_count} | MEDIUM: {m_count}"
                f"{rag_info}\n"
                f"  Extrait:\n{text[:1000]}"
                f"{'...' if len(text) > 1000 else ''}"
            )

        analyses_text = "\n".join(summaries)

        prompt = f"""Vous êtes un architecte logiciel expert. Créez un plan de refactoring GLOBAL et COHÉRENT.

═══ ANALYSES ({len(analysis_results)} fichiers) ═══

{analyses_text[:7000]}

═══ STATISTIQUES ═══
CRITICAL: {total_critical} | HIGH: {total_high} | MEDIUM: {total_medium}

MISSION: Créez un plan qui :
1. Priorise par impact réel (CRITICAL > HIGH > MEDIUM)
2. Identifie les dépendances entre corrections (corriger X avant Y)
3. Estime l'effort en heures/jours pour chaque phase
4. Organise les phases pour ne pas casser le projet en cours de refactoring

FORMAT REQUIS:

══ PHASE 1: SÉCURITÉ CRITIQUE (faire IMMÉDIATEMENT)
Impact: [fichiers/fonctionnalités affectés]
Corrections:
1. [Fichier.ext] - [Problème exact]
   → Raison: [pourquoi c'est critique]
   → Effort: [estimation temps]

══ PHASE 2: BUGS & ARCHITECTURE (cette semaine)
Dépendances: [phases préalables]
...

══ PHASE 3: QUALITÉ & PERFORMANCE (prochain sprint)
...

══ ORDRE D'EXÉCUTION OBLIGATOIRE
1. [Correction A] AVANT [B] car [raison technique]

══ RISQUES & MITIGATION
Risque: [description] → Mitigation: [comment l'éviter]

══ RECOMMANDATIONS FINALES
[Conseils pour l'équipe]

PLAN:"""

        try:
            response = self.llm.invoke(prompt)
            return response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error("Erreur génération plan refactoring : %s", e)
            return f" Erreur lors de la génération du plan: {e}"



assistant_agent = CodeRAGSystemAPI()