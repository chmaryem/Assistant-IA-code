"""
Incremental Analyzer - VERSION FINALE AVEC INDEXATION PROJET
Analyse uniquement les changements significatifs + Contexte projet complet
"""
from pathlib import Path
from typing import Dict, List, Any, Optional
from queue import Queue
import threading
import time
import difflib
import re
import hashlib

from code_parser import parser
from assistant_agent import assistant_agent
from dependency_graph import dependency_builder
from cache_manager import CacheManager
from project_indexer import get_project_index  # NOUVEAU


# ─────────────────────────────────────────────────────────────────────────────
# Codes ANSI — activés sur Windows PowerShell sans dépendance externe
# ─────────────────────────────────────────────────────────────────────────────

def _enable_windows_ansi():
    try:
        import ctypes, sys
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7
            )
    except Exception:
        pass

_enable_windows_ansi()

_R  = "\033[0m"       # reset
_BD = "\033[1m"       # bold
_DM = "\033[2m"       # dim
_RD = "\033[91m"      # rouge
_GR = "\033[92m"      # vert
_YL = "\033[93m"      # jaune
_CY = "\033[96m"      # cyan
_GY = "\033[90m"      # gris
_OR = "\033[38;5;208m"  # orange

_W   = 72
_SEP = "\u2500" * _W   # ─────
_SEP2= "\u2550" * _W   # ═════

_SEV = {
    "CRITICAL": (_RD,  "\U0001f534", "CRITIQUE"),
    "HIGH":     (_OR,  "\U0001f7e0", "HAUTE"),
    "MEDIUM":   (_YL,  "\U0001f7e1", "MOYENNE"),
    "LOW":      ("\033[94m", "\U0001f535", "FAIBLE"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Parser inline — extrait les blocs ---FIX START--- / ---FIX END---
# ─────────────────────────────────────────────────────────────────────────────

def _parse_fix_blocks(text: str) -> list:
    """Parse les blocs LLM. Aucune dépendance externe."""
    blocks = []
    parts = re.split(r'-{3,}\s*FIX START\s*-{3,}', text, flags=re.IGNORECASE)
    for raw in parts[1:]:
        end = re.search(r'-{3,}\s*FIX END\s*-{3,}', raw, re.IGNORECASE)
        if end:
            raw = raw[:end.start()]

        def _f(name):
            m = re.search(
                r'\*\*' + re.escape(name) + r'\*\*\s*:?\s*(.+?)(?=\n\s*\*\*|\Z)',
                raw, re.DOTALL | re.IGNORECASE
            )
            return m.group(1).strip() if m else ''

        def _code(section):
            m = re.search(
                r'\*\*' + re.escape(section) + r'\*\*.*?```\w*\n(.*?)```',
                raw, re.DOTALL | re.IGNORECASE
            )
            return m.group(1).rstrip() if m else ''

        sev_raw = _f("SEVERITY").upper().split()[0] if _f("SEVERITY") else "MEDIUM"
        severity = sev_raw if sev_raw in _SEV else "MEDIUM"
        location = _f("LOCATION")
        line_m   = re.search(r'[:\s](\d{1,5})\b', location)
        problem  = _f("PROBLEM")
        if not problem:
            continue
        blocks.append({
            "problem":      problem,
            "severity":     severity,
            "location":     location,
            "line_number":  int(line_m.group(1)) if line_m else None,
            "current_code": _code("CURRENT CODE"),
            "fixed_code":   _code("FIXED CODE"),
            "why":          _f("WHY"),
        })
    return blocks


def _make_diff(current: str, fixed: str) -> str:
    """Diff compact - / + (lignes réellement modifiées uniquement)."""
    if not current and not fixed:
        return ""
    cur_set = {l.strip() for l in current.splitlines() if l.strip()}
    fix_set = {l.strip() for l in fixed.splitlines()   if l.strip()}
    out = []
    for l in current.splitlines():
        if l.strip() in cur_set - fix_set:
            out.append(f"  {_RD}- {l}{_R}")
    for l in fixed.splitlines():
        if l.strip() in fix_set - cur_set:
            out.append(f"  {_GR}+ {l}{_R}")
    return "\n".join(out[:12])


def _print_block(block: dict, file_name: str) -> None:
    """Affiche un bloc de diagnostic formaté."""
    color, icon, label = _SEV.get(block["severity"], (_YL, "🟡", "MOYENNE"))
    loc      = block.get("location", "")
    line_num = block.get("line_number")

    print(f"\n{icon} [{_BD}{color}{label}{_R}] {_BD}{block['problem']}{_R}")
    if line_num:
        print(f"   \U0001f4cd {_CY}{file_name}:{line_num}{_R}  {_DM}({loc}){_R}")
    elif loc:
        print(f"   \U0001f4cd {_CY}{file_name}{_R}  {_DM}\u2192 {loc}{_R}")

    diff = _make_diff(block.get("current_code", ""), block.get("fixed_code", ""))
    if diff:
        print()
        print(diff)

    if block.get("why"):
        why = block["why"].replace("\n", " ").strip()
        if len(why) > 140:
            why = why[:137] + "\u2026"
        print(f"\n   \U0001f4a1 {why}")


def _print_results(text: str, file_name: str, context: dict,
                   elapsed: float, analyzed_count: int, score: int,
                   impacted: list) -> None:
    """
    Remplace le bloc '# RÉSULTATS COMPACTS' de _analyze_file().
    Parse les blocs LLM et affiche un tableau de bord compact.
    """
    blocks = _parse_fix_blocks(text)
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for b in blocks:
        counts[b["severity"]] = counts.get(b["severity"], 0) + 1

    # ── Barre de statut ───────────────────────────────────────────────────────
    from datetime import datetime
    now = datetime.now().strftime("%H:%M:%S")
    hc  = _RD if counts["CRITICAL"] else _OR if counts["HIGH"] else _YL if counts["MEDIUM"] else _GR

    parts = [
        f"{_DM}[{now}]{_R}",
        f"{_BD}{file_name}{_R}",
        f"Score: {_BD}{hc}{score}/100{_R}",
    ]
    if counts["CRITICAL"]: parts.append(f"\U0001f534 {_RD}{_BD}{counts['CRITICAL']} Critique(s){_R}")
    if counts["HIGH"]:     parts.append(f"\U0001f7e0 {_OR}{counts['HIGH']} Haute(s){_R}")
    if counts["MEDIUM"]:   parts.append(f"\U0001f7e1 {_YL}{counts['MEDIUM']} Moyenne(s){_R}")
    if not blocks:         parts.append(f"\U0001f7e2 {_GR}OK{_R}")
    parts.append(f"{_DM}{elapsed:.1f}s{_R}")

    print(f"\n{_DM}{_SEP}{_R}")
    print("  " + f"  {_DM}\u2502{_R}  ".join(parts))
    print(f"{_DM}{_SEP}{_R}")

    # ── Cas code correct ──────────────────────────────────────────────────────
    if not blocks:
        clean = text.strip()
        if any(k in clean for k in ("\u2705", "no major issues", "code quality is good")):
            print(f"\n  {_GR}\u2705  Aucun problème majeur détecté.{_R}\n")
        else:
            print(f"\n{clean}\n")
        print(f"{_DM}{_SEP2}{_R}")
        print(f"  {_DM}{elapsed:.1f}s  \u2502  Analysés : {analyzed_count}{_R}\n")
        return

    # ── Blocs de diagnostic ───────────────────────────────────────────────────
    for block in blocks:
        print(_SEP)
        _print_block(block, file_name)

    # ── Footer d'impact ───────────────────────────────────────────────────────
    print(f"\n{_DM}{_SEP}{_R}")
    if impacted:
        names = ", ".join(Path(p).name for p in impacted[:4])
        extra = f" +{len(impacted)-4}" if len(impacted) > 4 else ""
        print(f"\u26a0\ufe0f  {_YL}Impact sur {len(impacted)} dépendant(s) : {_BD}{names}{extra}{_R}")
    print(f"{_DM}{_SEP2}{_R}")
    print(f"  {_DM}{elapsed:.1f}s  \u2502  Analysés : {_BD}{analyzed_count}{_R}\n")


# ─────────────────────────────────────────────────────────────────────────────
# ChangeAnalyzer — identique à l'original
# ─────────────────────────────────────────────────────────────────────────────

class ChangeAnalyzer:
    """Analyse l'importance d'un changement pour décider s'il faut analyser"""
    
    @staticmethod
    def analyze_change(old_content: str, new_content: str) -> Dict[str, Any]:
        if not old_content and not new_content:
            return {'significant': False, 'score': 0, 'lines_changed': 0, 
                    'change_type': 'no_change', 'reason': 'Aucun contenu'}
        
        old_lines = old_content.splitlines() if old_content else []
        new_lines = new_content.splitlines()
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
        added   = [l[1:].strip() for l in diff if l.startswith('+') and not l.startswith('+++')]
        removed = [l[1:].strip() for l in diff if l.startswith('-') and not l.startswith('---')]
        lines_changed = len(added) + len(removed)
        change_type   = ChangeAnalyzer._classify_change(added, removed)
        score         = ChangeAnalyzer._calculate_score(change_type, lines_changed, added, removed)
        significant   = score >= 20
        reason        = ChangeAnalyzer._get_reason(change_type, lines_changed, significant)
        return {'significant': significant, 'score': score,
                'lines_changed': lines_changed, 'change_type': change_type, 'reason': reason}
    
    @staticmethod
    def _classify_change(added, removed):
        all_lines = added + removed
        if not all_lines: return 'no_change'
        non_empty = [l for l in all_lines if l]
        if not non_empty: return 'whitespace_only'
        if all(l.startswith(('import ', 'from ')) for l in non_empty): return 'import_only'
        if all(l.startswith(('#', '//', '/*', '*', '*/')) for l in non_empty): return 'comment_only'
        if all('"""' in l or "'''" in l for l in non_empty): return 'docstring_only'
        if any('def ' in l or 'class ' in l or 'function ' in l for l in added):
            if not any('def ' in l or 'class ' in l or 'function ' in l for l in removed):
                return 'new_function'
        if (any('def ' in l or 'class ' in l for l in added) and
                any('def ' in l or 'class ' in l for l in removed)):
            return 'function_signature'
        return 'logic_change'
    
    @staticmethod
    def _calculate_score(change_type, lines_changed, added, removed):
        base_score  = lines_changed * 10
        type_scores = {
            'import_only': 5, 'comment_only': 0, 'whitespace_only': 0,
            'docstring_only': 10, 'new_function': max(base_score, 50),
            'function_signature': max(base_score, 70), 'logic_change': max(base_score, 30)
        }
        return min(type_scores.get(change_type, base_score), 100)
    
    @staticmethod
    def _get_reason(change_type, lines_changed, significant):
        if not significant:
            reasons = {
                'import_only': f"Import seulement ({lines_changed} ligne(s))",
                'comment_only': "Commentaires seulement",
                'whitespace_only': "Formatage seulement",
                'docstring_only': "Documentation seulement",
                'no_change': "Aucun changement"
            }
            return reasons.get(change_type, f"Changement mineur ({lines_changed} ligne(s))")
        reasons = {
            'logic_change': f"Logique modifiée ({lines_changed} ligne(s))",
            'new_function': f"Nouvelle fonction ({lines_changed} ligne(s))",
            'function_signature': f"Signature modifiée ({lines_changed} ligne(s)) - Impact possible"
        }
        return reasons.get(change_type, f"Changement important ({lines_changed} ligne(s))")


# ─────────────────────────────────────────────────────────────────────────────
# IncrementalAnalyzer — logique 100% identique, seul l'affichage change
# ─────────────────────────────────────────────────────────────────────────────

class IncrementalAnalyzer:
    """Analyseur incrémental INTELLIGENT avec contexte projet"""
    
    def __init__(self, project_path: Path):
        self.project_path    = project_path
        self.cache           = CacheManager()
        self.dependency_graph = None
        self.analysis_queue  = Queue()
        self.worker_thread   = None
        self.is_running      = False
        self.project_indexer = None
        self.file_contents   = {}
        # Verrou : empêche deux affichages simultanés qui se chevauchent
        self._print_lock = threading.Lock()
        # Hash du dernier résultat affiché par fichier : évite la répétition
        self._last_hash: dict = {}
        self.stats = {
            "analyzed": 0, "skipped_hash": 0,
            "skipped_minor": 0, "time_total": 0, "by_type": {}
        }
    
    def initialize(self):
        """Initialisation avec indexation projet"""
        print(" Initialisation...")
        print(" Indexation du projet...")
        self.project_indexer  = get_project_index(self.project_path)
        self.dependency_graph = dependency_builder.build_from_project(self.project_path)
        nodes = self.dependency_graph.number_of_nodes()
        edges = self.dependency_graph.number_of_edges()
        print(f" Graphe : {nodes} nœuds, {edges} arêtes\n")
        self._start_worker()
    
    def _start_worker(self):
        self.is_running    = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
    
    def _worker_loop(self):
        while self.is_running:
            try:
                task = self.analysis_queue.get(timeout=1)
                if task.get("deleted"):
                    self._handle_deletion(task["file_path"])
                else:
                    self._analyze_file(task["file_path"])
                self.analysis_queue.task_done()
            except:
                pass
    
    def queue_analysis(self, file_path: Path, deleted: bool = False):
        if deleted:
            self.analysis_queue.put({"file_path": file_path, "deleted": True})
            return
        if not self.cache.has_file_changed(file_path):
            return
        self.analysis_queue.put({"file_path": file_path, "deleted": False})
    
    def _handle_deletion(self, file_path: Path):
        print(f"\n {file_path.name} supprimé")
        self.cache.remove_file_from_cache(file_path)
        node_id = f"file:{file_path}"
        if self.dependency_graph.has_node(node_id):
            self.dependency_graph.remove_node(node_id)
        self.cache.save()
        print()
    
    def _analyze_file(self, file_path: Path):
        """Analyse INTELLIGENTE d'un fichier avec contexte projet"""
        start = time.time()
        
        # Header compact (inchangé)
        print(f"\n{'─'*70}")
        print(f" {file_path.name}")
        
        # ÉTAPE 1: Hash check
        if not self.cache.has_file_changed(file_path):
            print("  Ignoré : Hash identique\n")
            self.stats["skipped_hash"] += 1
            return
        
        # ÉTAPE 2: Lire contenu
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                new_content = f.read()
        except Exception as e:
            print(f" Erreur : {e}\n")
            return
        
        # ÉTAPE 3: Analyser le changement
        old_content = self.file_contents.get(str(file_path), "")
        change_info = ChangeAnalyzer.analyze_change(old_content, new_content)
        
        print(f" {change_info['reason']} (score: {change_info['score']}/100)")
        
        # DÉCISION: Skip si non significatif
        if not change_info['significant']:
            print("  Analyse non nécessaire\n")
            self.stats["skipped_minor"] += 1
            self.stats["by_type"][change_info['change_type']] = \
                self.stats["by_type"].get(change_info['change_type'], 0) + 1
            self.cache.update_file_cache(
                file_path,
                {"analysis": "Changement mineur", "relevant_knowledge": []},
                [], []
            )
            self.cache.save()
            self.file_contents[str(file_path)] = new_content
            return
        
        print(" Analyse lancée")
        
        # ÉTAPE 4: Parsing
        parsed = parser.parse_file(file_path)
        if "error" in parsed:
            print(f" {parsed['error']}\n")
            return
        
        entities = len(parsed.get("entities", []))
        imports  = len(parsed.get("imports", []))
        print(f"   • {entities} entité(s), {imports} import(s)")
        
        # ÉTAPE 5: MAJ Graphe
        self._update_graph(file_path, parsed)
        
        # ÉTAPE 6: Impact
        impacted = self._find_impacted(file_path)
        if impacted:
            print(f" Impact : {len(impacted)} fichier(s)")
            for imp in impacted[:2]:
                print(f"   • {Path(imp).name}")
            if len(impacted) > 2:
                print(f"   • ... +{len(impacted)-2}")
        
        # ÉTAPE 7: Contexte
        context = self._build_context(file_path)
        crit_emoji = "🔴" if context['criticality'] > 5 else "🟡" if context['criticality'] > 0 else "🟢"
        print(f"{crit_emoji} Criticité : {context['criticality']}")
        
        # ÉTAPE 8: Contexte projet
        context['project_context'] = self.project_indexer.format_for_llm(file_path)
        context['change_type']     = change_info['change_type']
        context['lines_changed']   = change_info['lines_changed']
        
        # ÉTAPE 9: LLM
        print(" Analyse LLM...", flush=True)
        analysis = assistant_agent.analyze_code_with_rag(
            code=new_content,
            context=context
        )
        
        # ÉTAPE 10: Cache
        self.cache.update_file_cache(file_path, analysis, context['dependencies'], context['dependents'])
        self.cache.save()
        self.file_contents[str(file_path)] = new_content
        
        # ── RÉSULTATS : tableau de bord compact ──────────────────────────────
        elapsed = time.time() - start
        self.stats["analyzed"] += 1
        self.stats["time_total"] += elapsed
        self.stats["by_type"][change_info['change_type']] = \
            self.stats["by_type"].get(change_info['change_type'], 0) + 1

        # Déduplication : même résultat LLM → ne pas réafficher
        result_text = analysis['analysis']
        result_hash = hashlib.md5(result_text.encode('utf-8', errors='replace')).hexdigest()
        file_key    = str(file_path)
        if self._last_hash.get(file_key) == result_hash:
            return   # Watchdog a envoyé l'événement plusieurs fois, même contenu
        self._last_hash[file_key] = result_hash

        # Verrou : un seul affichage à la fois (évite les blocs qui se chevauchent)
        with self._print_lock:
            _print_results(
                text           = result_text,
                file_name      = file_path.name,
                context        = context,
                elapsed        = elapsed,
                analyzed_count = self.stats["analyzed"],
                score          = change_info['score'],
                impacted       = impacted,
            )
    
    def _update_graph(self, file_path, parsed):
        node_id = f"file:{file_path}"
        if self.dependency_graph.has_node(node_id):
            self.dependency_graph.remove_edges_from(list(self.dependency_graph.out_edges(node_id)))
        else:
            self.dependency_graph.add_node(node_id)
        for imp in parsed.get("imports", []):
            target = self._resolve_import(imp, file_path.parent)
            if target:
                t_node = f"file:{target}"
                if not self.dependency_graph.has_node(t_node):
                    self.dependency_graph.add_node(t_node)
                self.dependency_graph.add_edge(node_id, t_node, relation="imports")
    
    def _resolve_import(self, imp, current_dir):
        if not imp.module or not imp.module.startswith('.'):
            return None
        parts = imp.module.split('.')
        path  = current_dir
        for p in parts:
            path = path.parent if p == '' else path / p
        py_file = path.with_suffix('.py')
        return str(py_file) if py_file.exists() else None
    
    def _find_impacted(self, file_path):
        node_id = f"file:{file_path}"
        if not self.dependency_graph.has_node(node_id):
            return []
        return [n.replace("file:", "") for n in self.dependency_graph.predecessors(node_id)
                if n.startswith("file:")]
    
    def _build_context(self, file_path):
        node_id    = f"file:{file_path}"
        deps       = ([n.replace("file:", "") for n in self.dependency_graph.successors(node_id)
                       if n.startswith("file:")] if self.dependency_graph.has_node(node_id) else [])
        dependents = ([n.replace("file:", "") for n in self.dependency_graph.predecessors(node_id)
                       if n.startswith("file:")] if self.dependency_graph.has_node(node_id) else [])
        return {
            "dependencies":  deps,
            "dependents":    dependents,
            "criticality":   len(dependents),
            "is_entry_point": len(dependents) == 0
        }
    
    def stop(self):
        print("\n Arrêt...")
        self.is_running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        self.cache.save()
        print(f"\n Statistiques:")
        print(f"   • Analysés : {self.stats['analyzed']}")
        print(f"   • Ignorés (hash) : {self.stats['skipped_hash']}")
        print(f"   • Ignorés (mineur) : {self.stats['skipped_minor']}")
        if self.stats['by_type']:
            print(f"\n   Par type :")
            for t, c in sorted(self.stats['by_type'].items()):
                print(f"      • {t.replace('_', ' ').title()} : {c}")
        if self.stats['analyzed']:
            avg = self.stats['time_total'] / self.stats['analyzed']
            print(f"\n   Temps moyen : {avg:.1f}s")
        print()