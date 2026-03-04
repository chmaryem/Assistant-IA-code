import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from assistant_agent import assistant_agent
from project_analyzer import project_analyzer

# Imports pour le mode watch
from file_watcher import FileWatcher
from incremental_analyzer import IncrementalAnalyzer


class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    """Affiche un en-tête stylisé"""
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*80}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{text.center(80)}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*80}{Colors.ENDC}\n")


def print_success(text: str):
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")


def print_error(text: str):
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")


def print_warning(text: str):
    print(f"{Colors.WARNING} {text}{Colors.ENDC}")


def print_info(text: str):
    print(f"{Colors.OKCYAN} {text}{Colors.ENDC}")


def analyze_single_file(file_path: Path):
    """
    Analyse UN seul fichier avec le RAG
    
    Utilise: assistant_agent.analyze_code_with_rag()
    """
    print_header(f"ANALYSE FICHIER: {file_path.name}")
    
    # 1. Lire le fichier
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()
    except Exception as e:
        print_error(f"Impossible de lire le fichier: {e}")
        return
    
    print_info(f"Fichier: {file_path}")
    print_info(f"Taille: {len(code)} caractères\n")
    
    # 2. Déléguer l'analyse à assistant_agent (utilise le RAG automatiquement)
    print(" Analyse avec RAG en cours...")
    
    analysis = assistant_agent.analyze_code_with_rag(
        code=code,
        context={
            'file_path': str(file_path),
            'language': file_path.suffix.replace('.', '')
        }
    )
    
    # 3. Afficher les résultats
    print_header(" RÉSULTATS DE L'ANALYSE")
    print(analysis['analysis'])
    
    # 4. Afficher les best practices utilisées
    if analysis.get('relevant_knowledge'):
        print_header(" BEST PRACTICES CONSULTÉES")
        for knowledge in analysis['relevant_knowledge']:
            print(f"{knowledge.get('source', 'unknown')} ({knowledge.get('type', '')})")
    
    print_success("\n Analyse terminée")


def analyze_full_project(project_path: Path, max_files: int = 10):
    """
    Analyse COMPLÈTE d'un projet
    
    Utilise: project_analyzer.analyze_full_project()
    - Construit le graphe de dépendances
    - Identifie les fichiers critiques
    - Analyse avec contexte global
    - Détecte les conflits
    """
    print_header(f"ANALYSE PROJET COMPLET: {project_path.name}")
    
    # Déléguer TOUTE l'analyse à project_analyzer
    print(" Analyse en cours...\n")
    results = project_analyzer.analyze_full_project(project_path, max_files)
  
    print_header(" ARCHITECTURE DU PROJET")
    
    structure = results['structure_analysis']
    
    # Points d'entrée (fichiers qui ne sont utilisés par aucun autre)
    print(f"\n{Colors.BOLD} Points d'entrée:{Colors.ENDC}")
    entry_points = structure.get('entry_points', [])
    if entry_points:
        for entry in entry_points[:5]:
            # Extraire juste le nom du fichier
            file_name = entry.split(':')[-1] if ':' in entry else entry
            print(f"  • {file_name}")
        if len(entry_points) > 5:
            print(f"  ... et {len(entry_points) - 5} autres")
    else:
        print_info("  Aucun point d'entrée détecté")
    
    # Dépendances circulaires (A dépend de B qui dépend de A)
    print(f"\n{Colors.BOLD} Dépendances circulaires:{Colors.ENDC}")
    cycles = structure.get('circular_dependencies', [])
    if cycles:
        for cycle in cycles[:3]:
            cycle_str = ' → '.join([c.split(':')[-1] for c in cycle])
            print_warning(f"  {cycle_str}")
        if len(cycles) > 3:
            print_warning(f"  ... et {len(cycles) - 3} autres cycles")
    else:
        print_success("  Aucune dépendance circulaire ✓")
    
    # Modules orphelins (fichiers isolés, non connectés)
    print(f"\n{Colors.BOLD}  Modules orphelins:{Colors.ENDC}")
    orphans = structure.get('orphaned_modules', [])
    if orphans:
        for orphan in orphans[:5]:
            orphan_name = orphan.split(':')[-1] if ':' in orphan else orphan
            print_warning(f"  {orphan_name}")
        if len(orphans) > 5:
            print_warning(f"  ... et {len(orphans) - 5} autres")
    else:
        print_success("  Aucun module orphelin ✓")
    
   
    conflicts = results.get('conflicts', [])
    if conflicts:
        print_header("CONFLITS DE REFACTORING DÉTECTÉS")
        print_warning(f"  {len(conflicts)} conflit(s) trouvé(s)\n")
        
        for i, conflict in enumerate(conflicts, 1):
            print(f"{Colors.BOLD}Conflit #{i}:{Colors.ENDC}")
            print(f"  Type: {conflict['type']}")
            print(f"  Gravité: {conflict['severity']}")
            print(f"  Message: {conflict['message']}\n")
            
            if conflict.get('source_file'):
                print(f"  Fichier source: {conflict['source_file']}")
            if conflict.get('old_name') and conflict.get('new_name'):
                print(f"  Changement: {conflict['old_name']} → {conflict['new_name']}")
            if conflict.get('affected_files'):
                print(f"  Fichiers affectés ({len(conflict['affected_files'])}):")
                for affected in conflict['affected_files'][:3]:
                    print(f"    • {affected}")
                if len(conflict['affected_files']) > 3:
                    print(f"    ... et {len(conflict['affected_files']) - 3} autres")
            print()
    else:
        print_success("\n Aucun conflit détecté entre les corrections")
    
  
    print_header(" PLAN DE REFACTORING GLOBAL")
    refactoring_plan = results.get('refactoring_plan', '')
    print(refactoring_plan)
    
  
    print_header(" ANALYSES DÉTAILLÉES PAR FICHIER")
    
    file_analyses = results.get('file_analyses', {})
    
    for i, (file_path, analysis) in enumerate(file_analyses.items(), 1):
        print(f"\n{Colors.BOLD}{'─'*80}{Colors.ENDC}")
        print(f"{Colors.BOLD}Fichier {i}/{len(file_analyses)}: {Path(file_path).name}{Colors.ENDC}")
        print(f"{Colors.BOLD}{'─'*80}{Colors.ENDC}\n")
        
        # Contexte du fichier
        context = analysis.get('context', {})
        
        print(f"{Colors.OKCYAN}Contexte:{Colors.ENDC}")
        print(f"  • Criticité: {context.get('criticality_score', 0)} fichier(s) dépendent de celui-ci")
        print(f"  • Dépendances: {len(context.get('dependencies', []))} fichier(s)")
        print(f"  • Point d'entrée: {'Oui' if context.get('is_entry_point') else 'Non'}")
        
        # Best practices consultées
        if analysis.get('relevant_knowledge'):
            print(f"\n{Colors.OKCYAN}Best practices:{Colors.ENDC}")
            for kb in analysis['relevant_knowledge']:
                print(f"  • {kb.get('source', 'unknown')}")
        
        # Analyse détaillée
        print(f"\n{Colors.BOLD}Analyse:{Colors.ENDC}")
        print(analysis.get('analysis', 'Aucune analyse disponible'))
        print()
    
 
    print_header(" RÉSUMÉ DE L'ANALYSE")
    
    print(f"Fichiers critiques analysés: {Colors.BOLD}{len(file_analyses)}{Colors.ENDC}")
    print(f"Conflits détectés: {Colors.BOLD}{len(conflicts)}{Colors.ENDC}")
    print(f"Points d'entrée: {Colors.BOLD}{len(entry_points)}{Colors.ENDC}")
    print(f"Dépendances circulaires: {Colors.BOLD}{len(cycles)}{Colors.ENDC}")
    
    if conflicts:
        print(f"\n{Colors.WARNING}  ATTENTION: Résolvez les conflits avant d'appliquer les corrections{Colors.ENDC}")
    else:
        print(f"\n{Colors.OKGREEN} Vous pouvez appliquer les corrections en toute sécurité{Colors.ENDC}")
    
    print()


def watch_project(project_path: Path):
    """
    MODE WATCH - Surveille le projet en temps réel
    
    Nouveau mode qui:
    1. Initialise l'analyseur incrémental
    2. Démarre le file watcher
    3. Analyse automatiquement chaque modification
    """
    print_header(f"MODE WATCH - SURVEILLANCE TEMPS RÉEL")
    
    print(f" Projet: {project_path}")
    print(f" Mode: Analyse incrémentale\n")
    
    # Créer l'analyseur incrémental
    print(" Initialisation de l'analyseur incrémental...")
    analyzer = IncrementalAnalyzer(project_path)
    analyzer.initialize()
    
    # Callback pour les changements de fichiers
    def on_file_changed(file_path: Path, deleted: bool = False):
        """Appelé par le file watcher"""
        analyzer.queue_analysis(file_path, deleted=deleted)
    
    # Créer le file watcher
    watcher = FileWatcher(
        project_path=project_path,
        callback=on_file_changed
    )
    
    # Démarrer la surveillance (bloquant)
    # Note: watcher.watch() affichera ses propres messages
    try:
        watcher.watch()
    except KeyboardInterrupt:
        pass
    finally:
        # Arrêter proprement
        watcher.stop()
        analyzer.stop()


def check_api_connection():
    """Vérifie que l'API Groq est configurée"""
    print_info(" Vérification de la connexion Groq API...")
    
    try:
        # Test rapide
        assistant_agent.llm.invoke("test")
        print_success(" Groq API connectée\n")
        return True
    except Exception as e:
        print_error(f" Erreur de connexion: {e}\n")
        print_warning("Configuration requise:")
        print("  1. Créer un fichier .env")
        print("  2. Ajouter: GROQ_API_KEY=votre_clé")
        return False


def main():
    """
    Point d'entrée principal
    
    Rôle de main.py:
    - Parser les arguments CLI
    - Vérifier la connexion API
    - Déléguer à analyze_single_file() OU analyze_full_project() OU watch_project()
    - Afficher les résultats
    """
    
    # Configuration de l'interface CLI
    parser = argparse.ArgumentParser(
        description="Code Audit AI - Analyse intelligente avec RAG et graphe de dépendances",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples d'utilisation:
  python main.py file mon_script.py          # Analyser un fichier
  python main.py project ./mon_projet        # Analyser un projet complet
  python main.py watch ./mon_projet          # Mode surveillance temps réel (NOUVEAU)
        """
    )
    
    # Sous-commandes
    subparsers = parser.add_subparsers(dest='command', help='Mode d\'analyse')
    
    # Commande: file (un seul fichier)
    file_parser = subparsers.add_parser(
        'file',
        help='Analyser UN seul fichier avec RAG'
    )
    file_parser.add_argument(
        'path',
        type=str,
        help='Chemin vers le fichier à analyser'
    )
    
    # Commande: project (projet complet)
    project_parser = subparsers.add_parser(
        'project',
        help='Analyser un PROJET complet (graphe + RAG + conflits)'
    )
    project_parser.add_argument(
        'path',
        type=str,
        help='Chemin vers le dossier du projet'
    )
    project_parser.add_argument(
        '--max-files',
        type=int,
        default=10,
        help='Nombre maximum de fichiers critiques à analyser (défaut: 10)'
    )
    
    # Commande: watch (surveillance temps réel) - NOUVEAU
    watch_parser = subparsers.add_parser(
        'watch',
        help='SURVEILLER un projet en temps réel (analyse incrémentale)'
    )
    watch_parser.add_argument(
        'path',
        type=str,
        help='Chemin vers le dossier du projet à surveiller'
    )
    
    # Parser les arguments
    args = parser.parse_args()
    
    # Si aucune commande, afficher l'aide
    if not args.command:
        parser.print_help()
        return
    
    # Vérifier la connexion API
    if not check_api_connection():
        return
    
   
    
    if args.command == 'file':
        # Mode: UN seul fichier
        file_path = Path(args.path)
        
        if not file_path.exists():
            print_error(f"Fichier introuvable: {file_path}")
            return
        
        if not file_path.is_file():
            print_error(f"Ce n'est pas un fichier: {file_path}")
            return
        
        # Déléguer à analyze_single_file
        analyze_single_file(file_path)
    
    elif args.command == 'project':
        # Mode: PROJET complet
        project_path = Path(args.path)
        
        if not project_path.exists():
            print_error(f"Dossier introuvable: {project_path}")
            return
        
        if not project_path.is_dir():
            print_error(f"Ce n'est pas un dossier: {project_path}")
            return
        
        # Déléguer à analyze_full_project
        analyze_full_project(project_path, args.max_files)
    
    elif args.command == 'watch':
        # Mode: WATCH - Surveillance temps réel (NOUVEAU)
        project_path = Path(args.path)
        
        if not project_path.exists():
            print_error(f"Dossier introuvable: {project_path}")
            return
        
        if not project_path.is_dir():
            print_error(f"Ce n'est pas un dossier: {project_path}")
            return
        
        # Vérifier que watchdog est installé
        try:
            import watchdog
        except ImportError:
            print_error("Le module 'watchdog' n'est pas installé")
            print_info("Installation: pip install watchdog")
            return
        
        # Déléguer à watch_project
        watch_project(project_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}  Analyse interrompue par l'utilisateur{Colors.ENDC}")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Colors.FAIL} Erreur fatale: {e}{Colors.ENDC}")
        import traceback
        traceback.print_exc()
        sys.exit(1)