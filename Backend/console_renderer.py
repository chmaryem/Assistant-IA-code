"""
console_renderer.py — Tableau de bord console "Pro" pour Code Audit AI
=======================================================================

Transforme le texte brut du LLM en sortie structurée lisible en 2 secondes.

Format de sortie :
────────────────────────────────────────────────────────────────────────────────
[14:30:22] 📂 UserService.java  │  Score: 85/100  │  🔴 2 Critiques  │  🟠 1 Haute  │  8.4s
────────────────────────────────────────────────────────────────────────────────

🔴 [CRITIQUE] INJECTION SQL — authenticate()
📍 UserService.java:52

  51 │  public boolean authenticate(String username, String password) {
  52 │      String query = "SELECT * FROM users WHERE username='" + username + "'";
     │                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  53 │      Statement stmt = dbConnection.createStatement();

  - String query = "SELECT * FROM users WHERE username='" + username + "'";
  + String query = "SELECT * FROM users WHERE username = ?";
  + stmt.setString(1, username);

  💡 L'entrée n'est pas assainie — un attaquant peut vider toute la table users.

────────────────────────────────────────────────────────────────────────────────
⚠️  Impact détecté sur 1 dépendant(s) : UserController.java
════════════════════════════════════════════════════════════════════════════════
  8.4s  │  Analysés : 1
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Codes ANSI — compatibles Windows PowerShell (sans dépendance externe)
# ─────────────────────────────────────────────────────────────────────────────

class _C:
    RST    = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    GREY   = "\033[90m"
    ORANGE = "\033[38;5;208m"


def _enable_windows_ansi() -> None:
    try:
        import ctypes, sys
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7
            )
    except Exception:
        pass


_enable_windows_ansi()

# ─────────────────────────────────────────────────────────────────────────────
# Constantes visuelles
# ─────────────────────────────────────────────────────────────────────────────

_W    = 80
_SEP  = "\u2500" * _W   # ─────  (séparateur fin entre les blocs)
_SEP2 = "\u2550" * _W   # ═════  (séparateur épais fin d'analyse)


# ─────────────────────────────────────────────────────────────────────────────
# Mapping sévérité → style
# ─────────────────────────────────────────────────────────────────────────────

_SEV: dict[str, dict] = {
    "CRITICAL": {"icon": "\U0001f534", "label": "CRITIQUE", "color": _C.RED   },
    "HIGH":     {"icon": "\U0001f7e0", "label": "HAUTE",    "color": _C.ORANGE},
    "MEDIUM":   {"icon": "\U0001f7e1", "label": "MOYENNE",  "color": _C.YELLOW},
    "LOW":      {"icon": "\U0001f535", "label": "FAIBLE",   "color": _C.BLUE  },
}


# ─────────────────────────────────────────────────────────────────────────────
# Parser LLM : extrait les blocs ---FIX START--- / ---FIX END---
# ─────────────────────────────────────────────────────────────────────────────

def _parse_blocks(text: str) -> list[dict]:
    blocks: list[dict] = []
    parts = re.split(r"-{3,}\s*FIX START\s*-{3,}", text, flags=re.IGNORECASE)

    for raw in parts[1:]:
        end = re.search(r"-{3,}\s*FIX END\s*-{3,}", raw, re.IGNORECASE)
        if end:
            raw = raw[: end.start()]

        def _field(name: str) -> str:
            m = re.search(
                r"\*\*" + re.escape(name) + r"\*\*\s*:?\s*(.+?)(?=\n\s*\*\*|\Z)",
                raw, re.DOTALL | re.IGNORECASE,
            )
            return m.group(1).strip() if m else ""

        def _code(section: str) -> str:
            m = re.search(
                r"\*\*" + re.escape(section) + r"\*\*.*?```\w*\n(.*?)```",
                raw, re.DOTALL | re.IGNORECASE,
            )
            return m.group(1).rstrip() if m else ""

        sev_raw  = _field("SEVERITY").upper().split()[0] if _field("SEVERITY") else "MEDIUM"
        severity = sev_raw if sev_raw in _SEV else "MEDIUM"

        location   = _field("LOCATION")
        line_match = re.search(r"[:\s](\d{1,5})\b", location)
        line_num   = int(line_match.group(1)) if line_match else None

        problem = _field("PROBLEM")
        if not problem:
            continue

        blocks.append({
            "problem":      problem,
            "severity":     severity,
            "location":     location,
            "line_number":  line_num,
            "current_code": _code("CURRENT CODE"),
            "fixed_code":   _code("FIXED CODE"),
            "why":          _field("WHY"),
        })

    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Snippet pointeur : 3 lignes avec ^^^^^ sous le token dangereux
# ─────────────────────────────────────────────────────────────────────────────

def _make_snippet(full_code: str, current_code: str, hint_line: int | None) -> str:
    if not full_code:
        return ""

    all_lines = full_code.splitlines()
    total     = len(all_lines)
    target: int | None = None

    if hint_line and 1 <= hint_line <= total:
        target = hint_line
    elif current_code:
        first = current_code.splitlines()[0].strip()
        if len(first) >= 8:
            for i, line in enumerate(all_lines):
                if first[:40] in line:
                    target = i + 1
                    break

    if target is None:
        return ""

    ctx_start = max(0,     target - 2)
    ctx_end   = min(total, target + 1)
    out: list[str] = []

    for i in range(ctx_start, ctx_end):
        lineno  = i + 1
        content = all_lines[i]
        num_s   = _C.GREY + f"{lineno:4d}" + _C.RST
        bar_s   = _C.GREY + " \u2502 " + _C.RST

        if lineno == target:
            out.append(f"  {num_s}{bar_s}{_C.RED}{content}{_C.RST}")
            first_tok = current_code.splitlines()[0].strip()[:25] if current_code else ""
            col = content.find(first_tok) if first_tok else -1
            if col >= 0:
                pad    = " " * (col + 10)
                carets = "^" * min(len(first_tok) + 8, _W - col - 12)
                out.append(f"  {'':4s}   {_C.RED}{pad}{carets}{_C.RST}")
        else:
            out.append(f"  {num_s}{bar_s}{_C.DIM}{content}{_C.RST}")

    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Diff compact : - lignes supprimées / + lignes ajoutées
# ─────────────────────────────────────────────────────────────────────────────

def _make_diff(current: str, fixed: str, max_lines: int = 10) -> str:
    if not current and not fixed:
        return ""

    cur_set = {l.strip() for l in current.splitlines() if l.strip()}
    fix_set = {l.strip() for l in fixed.splitlines()   if l.strip()}

    removed = [l for l in current.splitlines() if l.strip() in cur_set - fix_set]
    added   = [l for l in fixed.splitlines()   if l.strip() in fix_set - cur_set]

    out: list[str] = []
    shown = 0
    for line in removed[:max_lines]:
        out.append(f"  {_C.RED}- {line}{_C.RST}")
        shown += 1
    for line in added[: max(max_lines - shown, 2)]:
        out.append(f"  {_C.GREEN}+ {line}{_C.RST}")

    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Rendu d'un seul bloc de diagnostic
# ─────────────────────────────────────────────────────────────────────────────

def _render_block(block: dict, file_path: Path, full_code: str) -> None:
    sev   = _SEV.get(block["severity"], _SEV["MEDIUM"])
    color = sev["color"]
    icon  = sev["icon"]
    label = sev["label"]

    print(_SEP)
    print()
    print(f"{icon} [{_C.BOLD}{color}{label}{_C.RST}] {_C.BOLD}{block['problem']}{_C.RST}")

    loc      = block.get("location", "")
    line_num = block.get("line_number")
    if line_num:
        print(f"\U0001f4cd {_C.CYAN}{file_path.name}:{line_num}{_C.RST}  {_C.DIM}({loc}){_C.RST}")
    elif loc:
        print(f"\U0001f4cd {_C.CYAN}{file_path.name}{_C.RST}  {_C.DIM}\u2192 {loc}{_C.RST}")
    print()

    snippet = _make_snippet(full_code, block["current_code"], block["line_number"])
    if snippet:
        print(snippet)
        print()

    diff = _make_diff(block["current_code"], block["fixed_code"])
    if diff:
        print(diff)
        print()

    if block.get("why"):
        why = block["why"].replace("\n", " ").strip()
        if len(why) > 140:
            why = why[:137] + "\u2026"
        print(f"  \U0001f4a1 {_C.WHITE}{why}{_C.RST}")


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée public
# ─────────────────────────────────────────────────────────────────────────────

def render_analysis(
    file_path:      Path,
    analysis:       dict[str, Any],
    context:        dict[str, Any],
    change_info:    dict[str, Any],
    elapsed:        float,
    analyzed_count: int,
) -> None:
    text      = analysis.get("analysis", "")
    full_code = analysis.get("code",     "")
    impacted  = context.get("dependents", [])
    score     = change_info.get("score", 0)

    blocks = _parse_blocks(text)

    counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for b in blocks:
        counts[b["severity"]] = counts.get(b["severity"], 0) + 1

    health_color = (_C.RED    if counts["CRITICAL"] else
                    _C.ORANGE if counts["HIGH"]     else
                    _C.YELLOW if counts["MEDIUM"]   else _C.GREEN)

    # Barre de statut
    now   = datetime.now().strftime("%H:%M:%S")
    parts = [
        f"{_C.DIM}[{now}]{_C.RST}",
        f"\U0001f4c2 {_C.BOLD}{file_path.name}{_C.RST}",
        f"Score: {_C.BOLD}{health_color}{score}/100{_C.RST}",
    ]
    if counts["CRITICAL"]:
        parts.append(f"\U0001f534 {_C.RED}{_C.BOLD}{counts['CRITICAL']} Critique(s){_C.RST}")
    if counts["HIGH"]:
        parts.append(f"\U0001f7e0 {_C.ORANGE}{counts['HIGH']} Haute(s){_C.RST}")
    if counts["MEDIUM"]:
        parts.append(f"\U0001f7e1 {_C.YELLOW}{counts['MEDIUM']} Moyenne(s){_C.RST}")
    if counts["LOW"]:
        parts.append(f"\U0001f535 {_C.BLUE}{counts['LOW']} Faible(s){_C.RST}")
    if not blocks:
        parts.append(f"\U0001f7e2 {_C.GREEN}OK{_C.RST}")
    parts.append(f"{_C.DIM}{elapsed:.1f}s{_C.RST}")

    print(f"\n{_C.DIM}{_SEP}{_C.RST}")
    print("  " + f"  {_C.DIM}\u2502{_C.RST}  ".join(parts))
    print(f"{_C.DIM}{_SEP}{_C.RST}")

    # Code OK
    if not blocks:
        clean = text.strip()
        if any(kw in clean for kw in ("\u2705", "no major issues", "code quality is good", "no issues")):
            print(f"\n  {_C.GREEN}\u2705  Aucun problème majeur détecté.{_C.RST}\n")
        else:
            print(f"\n{clean}\n")
        print(f"{_C.DIM}{_SEP2}{_C.RST}")
        print(f"  {_C.DIM}{elapsed:.1f}s  \u2502  Analysés : {analyzed_count}{_C.RST}\n")
        return

    # Blocs de diagnostic
    for block in blocks:
        _render_block(block, file_path, full_code)
        print()

    # Footer d'impact
    if impacted:
        names = ", ".join(Path(p).name for p in impacted[:4])
        extra = f" +{len(impacted) - 4}" if len(impacted) > 4 else ""
        print(f"{_C.DIM}{_SEP}{_C.RST}")
        print(
            f"\u26a0\ufe0f  {_C.YELLOW}Impact détecté sur {len(impacted)} dépendant(s) : "
            f"{_C.BOLD}{names}{extra}{_C.RST}"
        )
        print()

    print(f"{_C.DIM}{_SEP2}{_C.RST}")
    print(f"  {_C.DIM}{elapsed:.1f}s  \u2502  Analysés : {_C.BOLD}{analyzed_count}{_C.RST}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Affichage "skip" — silencieux, une ligne grisée
# ─────────────────────────────────────────────────────────────────────────────

def render_skip(file_path: Path, reason: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"  {_C.DIM}[{now}]  \u23ed  {file_path.name}  \u2014  {reason}{_C.RST}")


# ─────────────────────────────────────────────────────────────────────────────
# Déduplicateur — corrige le bug "résultat affiché 6 fois"
# ─────────────────────────────────────────────────────────────────────────────

class DisplayDeduplicator:
    """
    Watchdog peut émettre 3-6 événements pour une seule sauvegarde.
    Compare le MD5 du résultat LLM pour chaque fichier :
      - Identique au précédent → ne pas réafficher (return False)
      - Nouveau contenu       → afficher et mémoriser (return True)
    """

    def __init__(self) -> None:
        self._last: dict[str, str] = {}

    def should_display(self, file_path: Path, analysis_text: str) -> bool:
        key = str(file_path)
        h   = hashlib.md5(analysis_text.encode("utf-8", errors="replace")).hexdigest()
        if self._last.get(key) == h:
            return False
        self._last[key] = h