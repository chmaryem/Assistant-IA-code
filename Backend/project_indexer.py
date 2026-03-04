"""
Project Indexer OPTIMISÉ - Réutilise dependency_graph.py
Évite la duplication en utilisant le travail déjà fait par dependency_graph
"""
from pathlib import Path
from typing import Dict, List, Any, Set
import json
from dataclasses import dataclass, asdict
from dependency_graph import dependency_builder


@dataclass
class ProjectContext:
    """Contexte complet du projet avec toutes les métadonnées"""
    total_files: int
    total_entities: int
    languages: Dict[str, int]
    packages: List[str]  # Packages/directories internes
    files: Dict[str, Dict]  # file_path -> {entities, imports, language, criticality}
    architecture_info: Dict[str, Any]  # From dependency_graph


class ProjectIndexer:
    """
    Indexeur OPTIMISÉ qui réutilise dependency_graph
    
    Workflow:
    1. Appelle dependency_builder.build_from_project()
    2. Récupère file_entities, file_imports depuis le builder
    3. Enrichit avec packages, criticité depuis le graphe
    4. Sauvegarde TOUT dans cache JSON
    5. Charge en 0.5s les fois suivantes
    """
    
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.context: ProjectContext = None
        self.cache_file = project_path / '.codeaudit' / 'project_context.json'
    
    def build_index(self, dependency_graph=None, force_rebuild: bool = False) -> ProjectContext:
        """
        Construit l'index complet en réutilisant dependency_graph
        
        Args:
            dependency_graph: Graphe NetworkX déjà construit (optionnel)
            force_rebuild: Force reconstruction (ignore cache)
        
        Returns:
            ProjectContext avec toutes les infos
        """
        # Essayer de charger depuis cache
        if not force_rebuild and self._load_from_cache():
            return self.context
        
        print("🔍 Indexation du projet...")
        
        # Si pas de graphe fourni, le construire
        if dependency_graph is None:
            print("   • Construction du graphe de dépendances...")
            dependency_graph = dependency_builder.build_from_project(self.project_path)
        
        # Récupérer les données DÉJÀ parsées par dependency_builder
        file_entities = dependency_builder.file_entities
        file_imports = dependency_builder.file_imports
        
        print(f"   • {len(file_entities)} fichiers indexés")
        
        # Analyser l'architecture
        architecture_info = dependency_builder.analyze_flows()
        
        # Extraire packages/directories internes
        packages = self._extract_packages(file_entities.keys())
        
        # Calculer criticité depuis le graphe
        coupling_metrics = architecture_info['coupling_metrics']
        
        # Construire l'index des fichiers
        files_index = {}
        languages = {}
        total_entities = 0
        
        for file_path, entities in file_entities.items():
            # Déterminer le langage
            language = self._detect_language(Path(file_path))
            languages[language] = languages.get(language, 0) + 1
            
            # Entités formatées
            entities_list = []
            for entity in entities:
                entities_list.append({
                    'name': entity.name,
                    'type': entity.type,
                    'start_line': entity.start_line,
                    'end_line': entity.end_line,
                    'parameters': entity.parameters if hasattr(entity, 'parameters') else []
                })
            
            total_entities += len(entities_list)
            
            # Imports
            imports = [imp.module for imp in file_imports.get(file_path, [])]
            
            # Criticité depuis le graphe
            node_id = f"file:{file_path}"
            criticality = 0
            if node_id in coupling_metrics:
                criticality = coupling_metrics[node_id]['afferent']
            
            # Stocker tout
            files_index[file_path] = {
                'entities': entities_list,
                'imports': imports,
                'language': language,
                'criticality': criticality,
                'entity_count': len(entities_list)
            }
        
        # Créer le contexte complet
        self.context = ProjectContext(
            total_files=len(file_entities),
            total_entities=total_entities,
            languages=languages,
            packages=sorted(packages),
            files=files_index,
            architecture_info={
                'entry_points_count': len(architecture_info['entry_points']),
                'circular_deps_count': len(architecture_info['circular_dependencies']),
                'orphaned_count': len(architecture_info['orphaned_modules'])
            }
        )
        
        # Sauvegarder dans cache
        self._save_to_cache()
        
        print(f"✅ Indexation terminée : {self.context.total_files} fichiers\n")
        
        return self.context
    
    def _extract_packages(self, file_paths: List[str]) -> Set[str]:
        """Extrait les packages/directories internes du projet"""
        packages = set()
        
        for file_path_str in file_paths:
            try:
                file_path = Path(file_path_str)
                rel_dir = file_path.relative_to(self.project_path).parent
                dir_str = str(rel_dir).replace('\\', '/')
                
                if dir_str != '.':
                    packages.add(dir_str)
                    
                    # Pour Java: convertir src/main/java/com/example → com.example
                    if 'src/main/java/' in dir_str:
                        java_pkg = dir_str.split('src/main/java/')[-1].replace('/', '.')
                        packages.add(java_pkg)
                    
                    # Pour Python: convertir src/package/subpackage → package.subpackage
                    if '/' in dir_str:
                        python_pkg = dir_str.replace('/', '.')
                        packages.add(python_pkg)
            
            except ValueError:
                pass
        
        return packages
    
    def _detect_language(self, file_path: Path) -> str:
        """Détecte le langage d'un fichier"""
        ext_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.jsx': 'javascript',
            '.ts': 'typescript',
            '.tsx': 'typescript',
            '.java': 'java'
        }
        return ext_map.get(file_path.suffix, 'unknown')
    
    def _save_to_cache(self):
        """Sauvegarde le contexte dans cache JSON"""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Convertir en dict pour JSON
        cache_data = {
            'total_files': self.context.total_files,
            'total_entities': self.context.total_entities,
            'languages': self.context.languages,
            'packages': self.context.packages,
            'files': self.context.files,
            'architecture_info': self.context.architecture_info
        }
        
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2)
        
        print(f"💾 Cache sauvegardé : {self.cache_file}")
    
    def _load_from_cache(self) -> bool:
        """Charge le contexte depuis le cache"""
        if not self.cache_file.exists():
            return False
        
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            self.context = ProjectContext(**cache_data)
            
            print(f"✅ Index chargé depuis cache : {self.context.total_files} fichiers\n")
            return True
        
        except Exception as e:
            print(f"⚠️  Erreur chargement cache : {e}")
            return False
    
    # Suffixes par langage pour détection universelle
    LANGUAGE_SUFFIXES = {
        'java': [
            'service', 'controller', 'repository', 'dto', 'entity', 'model',
            'mapper', 'dao', 'impl', 'config', 'exception', 'request', 
            'response', 'validator', 'helper', 'util'
        ],
        'python': [
            'service', 'controller', 'repository', 'model', 'dao', 'helper',
            'utils', 'config', 'views', 'serializer', 'schema', 'handler',
            'manager', 'middleware', 'forms', 'admin', 'tests', 'factory'
        ],
        'javascript': [
            'service', 'controller', 'component', 'module', 'repository',
            'model', 'helper', 'provider', 'guard', 'interceptor', 'pipe',
            'middleware', 'store', 'action', 'reducer', 'saga', 'context',
            'hook', 'utils', 'config', 'constants'
        ],
        'typescript': [
            'service', 'controller', 'component', 'module', 'repository',
            'model', 'entity', 'dto', 'interface', 'type', 'guard',
            'interceptor', 'pipe', 'middleware', 'resolver', 'decorator'
        ]
    }
    
    def get_related_files(self, file_path: Path) -> List[str]:
        """
        Trouve les fichiers liés - UNIVERSEL pour TOUS les langages
        
        Conventions supportées :
        - Java: User.java → UserService.java, UserController.java
        - Python: user.py → user_service.py, user_controller.py
        - TypeScript: user.entity.ts → user.service.ts, user.controller.ts
        - JavaScript: User.jsx → UserService.js, user-utils.js
        """
        file_lang = self._detect_language(file_path)
        base_name = self._extract_base_name(file_path.stem, file_lang)
        
        related = []
        for indexed_file_path in self.context.files.keys():
            if indexed_file_path == str(file_path):
                continue
            
            indexed_stem = Path(indexed_file_path).stem
            indexed_lang = self.context.files[indexed_file_path]['language']
            
            if self._is_related(base_name, indexed_stem, file_lang, indexed_lang):
                related.append(indexed_file_path)
        
        return related[:5]
    
    def _extract_base_name(self, file_stem: str, language: str) -> str:
        """
        Extrait le nom de base selon les conventions du langage
        
        Examples:
            Java: UserService → user
            Python: user_service → user
            JavaScript: userService → user
            TypeScript: user.service → user
        """
        stem_lower = file_stem.lower()
        
        # 1. Notation avec points (TypeScript: user.service)
        if '.' in stem_lower:
            parts = stem_lower.split('.')
            if len(parts) >= 2:
                potential_suffix = parts[1]
                suffixes = self.LANGUAGE_SUFFIXES.get(language, [])
                if potential_suffix in suffixes:
                    return parts[0]
        
        # 2. Suffixes selon le langage
        suffixes = self.LANGUAGE_SUFFIXES.get(language, [])
        
        for suffix in suffixes:
            # Python: user_service → user
            if language == 'python' and stem_lower.endswith(f'_{suffix}'):
                return stem_lower[:-len(suffix)-1]
            
            # JavaScript/TypeScript kebab-case: user-service → user
            elif language in ['javascript', 'typescript'] and stem_lower.endswith(f'-{suffix}'):
                return stem_lower[:-len(suffix)-1]
            
            # CamelCase: userService → user
            elif stem_lower.endswith(suffix):
                return stem_lower[:-len(suffix)]
        
        return stem_lower
    
    def _is_related(self, base_name: str, candidate_stem: str, base_lang: str, candidate_lang: str) -> bool:
        """
        Vérifie si un fichier est lié - Supporte TOUTES les conventions
        
        Fonctionne inter-langages et inter-conventions :
        - user matches user_service, user-service, user.service, userService
        """
        base_lower = base_name.lower()
        candidate_lower = candidate_stem.lower()
        
        # Stratégie 1 : Normalisation complète
        # Retire tous les séparateurs pour matcher entre conventions
        base_normalized = base_lower.replace('_', '').replace('-', '').replace('.', '')
        candidate_normalized = candidate_lower.replace('_', '').replace('-', '').replace('.', '')
        
        if candidate_normalized.startswith(base_normalized) and len(candidate_normalized) > len(base_normalized):
            return True
        
        # Stratégie 2 : Correspondance avec séparateurs
        patterns = [
            f"{base_lower}_",   # Python: user_
            f"{base_lower}-",   # Kebab: user-
            f"{base_lower}.",   # Dot: user.
            base_lower          # CamelCase: userService
        ]
        
        for pattern in patterns:
            if candidate_lower.startswith(pattern):
                return True
        
        # Stratégie 3 : Dans les parties séparées
        for sep in ['_', '-', '.']:
            if sep in candidate_lower:
                parts = candidate_lower.split(sep)
                if base_lower in parts:
                    return True
        
        return False
    
    def format_for_llm(self, target_file: Path = None) -> str:
        """
        Formate le contexte pour le LLM
        
        Inclut:
        - Statistiques globales
        - Packages existants
        - Fichiers liés (si target_file fourni)
        - Architecture (entry points, cycles, etc.)
        """
        lines = []
        lines.append("=" * 70)
        lines.append("PROJECT CONTEXT")
        lines.append("=" * 70)
        lines.append("")
        
        # Statistiques
        lines.append("PROJECT SUMMARY:")
        lines.append(f"  • {self.context.total_files} files indexed")
        lines.append(f"  • {self.context.total_entities} total entities")
        lines.append(f"  • Languages: {', '.join(f'{k} ({v})' for k, v in self.context.languages.items())}")
        
        # Packages internes
        if self.context.packages:
            packages_str = ', '.join(self.context.packages[:30])
            if len(self.context.packages) > 30:
                packages_str += f' ... and {len(self.context.packages) - 30} more'
            lines.append(f"  • Existing Internal Packages/Dirs: {packages_str}")
        
        # Architecture
        arch = self.context.architecture_info
        lines.append(f"  • Entry points: {arch['entry_points_count']}")
        lines.append(f"  • Circular dependencies: {arch['circular_deps_count']}")
        lines.append(f"  • Orphaned modules: {arch['orphaned_count']}")
        lines.append("")
        
        # Fichiers liés si target_file fourni
        if target_file:
            related_files = self.get_related_files(target_file)
            
            if related_files:
                lines.append("=" * 70)
                lines.append(f"RELATED FILES FOR: {target_file.name}")
                lines.append("=" * 70)
                lines.append("")
                
                for related_path in related_files:
                    file_info = self.context.files[related_path]
                    file_name = Path(related_path).name
                    
                    lines.append(f"FILE: {file_name}")
                    lines.append(f"  Language: {file_info['language']}")
                    lines.append(f"  Criticality: {file_info['criticality']} (files depend on this)")
                    
                    entities = file_info['entities']
                    if entities:
                        lines.append(f"  Entities ({len(entities)}):")
                        for entity in entities[:10]:
                            entity_type = entity['type']
                            entity_name = entity['name']
                            
                            if entity.get('parameters'):
                                params = ', '.join(entity['parameters'][:3])
                                if len(entity['parameters']) > 3:
                                    params += ', ...'
                                lines.append(f"    • {entity_type}: {entity_name}({params})")
                            else:
                                lines.append(f"    • {entity_type}: {entity_name}")
                        
                        if len(entities) > 10:
                            lines.append(f"    ... and {len(entities) - 10} more")
                    
                    lines.append("")
        
        lines.append("=" * 70)
        lines.append("⚠️  IMPORTANT:")
        lines.append("• These files/packages ALREADY EXIST in the project")
        lines.append("• Do NOT suggest creating new files/classes that exist")
        lines.append("• Suggest using EXISTING entities shown above")
        lines.append("• Follow the existing project structure and packages")
        lines.append("=" * 70)
        
        return "\n".join(lines)
    
    def get_file_criticality(self, file_path: Path) -> int:
        """Récupère la criticité d'un fichier"""
        file_info = self.context.files.get(str(file_path), {})
        return file_info.get('criticality', 0)


# Fonction globale pour intégration facile
_project_indexer = None


def get_project_index(project_path: Path, dependency_graph=None, force_rebuild: bool = False) -> ProjectIndexer:
    """
    Récupère l'indexeur de projet (avec cache)
    
    Usage dans incremental_analyzer.py:
        # Au démarrage
        self.dependency_graph = dependency_builder.build_from_project(project_path)
        self.project_indexer = get_project_index(project_path, self.dependency_graph)
        
        # Dans _analyze_file()
        context['project_context'] = self.project_indexer.format_for_llm(file_path)
    """
    global _project_indexer
    
    if _project_indexer is None:
        _project_indexer = ProjectIndexer(project_path)
    
    _project_indexer.build_index(dependency_graph, force_rebuild)
    
    return _project_indexer