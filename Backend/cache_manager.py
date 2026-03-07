import hashlib
import pickle
import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from config import config


class CacheManager:

    
    def __init__(self, cache_dir: Path = None):
        """
        Args:
            cache_dir: Répertoire de stockage du cache 
        """
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.cache_file = self.cache_dir / "analysis_cache.pkl"
        
        # Créer le répertoire si nécessaire
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Structure du cache
        self.cache: Dict[str, Any] = {
            "files": {},
            "metadata": {
                "version": "1.0",
                "last_updated": None
            }
        }
        
        # Charger le cache existant
        self.load()
    
    def compute_file_hash(self, file_path: Path) -> str:
        """
        Calcule le hash SHA256 du contenu d'un fichier
        
        Args:
            file_path: Chemin du fichier
            
        Returns:
            Hash hexadécimal du contenu
        """
        hasher = hashlib.sha256()
        
        try:
            with open(file_path, 'rb') as f:
                # Lire par blocs pour les gros fichiers
                while chunk := f.read(8192):
                    hasher.update(chunk)
            
            return hasher.hexdigest()
        
        except Exception as e:
            print(f" Erreur calcul hash pour {file_path}: {e}")
            return ""
    
    def has_file_changed(self, file_path: Path) -> bool:
        """
        Vérifie si un fichier a vraiment changé depuis la dernière analyse
        
        Args:
            file_path: Chemin du fichier
            
        Returns:
            True si le contenu a changé (ou fichier nouveau)
        """
        file_key = str(file_path)
        
        # Si fichier pas dans le cache → nouveau fichier
        if file_key not in self.cache["files"]:
            return True
        
        # Calculer le hash actuel
        current_hash = self.compute_file_hash(file_path)
        
        # Comparer avec le hash en cache
        cached_hash = self.cache["files"][file_key].get("hash", "")
        
        return current_hash != cached_hash
    
    def get_cached_analysis(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        Récupère l'analyse en cache d'un fichier
        
        Args:
            file_path: Chemin du fichier
            
        Returns:
            Dict contenant l'analyse, ou None si pas en cache
        """
        file_key = str(file_path)
        
        if file_key not in self.cache["files"]:
            return None
        
        return self.cache["files"][file_key].get("last_analysis")
    
    def update_file_cache(
        self,
        file_path: Path,
        analysis: Dict[str, Any],
        dependencies: list = None,
        dependents: list = None
    ):
        """
        Met à jour le cache pour un fichier
        
        Args:
            file_path: Chemin du fichier
            analysis: Résultat de l'analyse
            dependencies: Liste des fichiers dont dépend ce fichier
            dependents: Liste des fichiers qui dépendent de ce fichier
        """
        file_key = str(file_path)
        
        # Calculer le hash actuel
        current_hash = self.compute_file_hash(file_path)
        
        # Timestamp actuel
        timestamp = datetime.now().timestamp()
        
        # Mettre à jour ou créer l'entrée
        self.cache["files"][file_key] = {
            "hash": current_hash,
            "last_modified": timestamp,
            "last_analysis": {
                "timestamp": timestamp,
                "analysis": analysis.get("analysis", ""),
                "relevant_knowledge": analysis.get("relevant_knowledge", []),
                "context": analysis.get("context", {})
            },
            "dependencies": dependencies or [],
            "dependents": dependents or []
        }
        
        # Mettre à jour les métadonnées
        self.cache["metadata"]["last_updated"] = timestamp
    
    def remove_file_from_cache(self, file_path: Path):
        """
        Supprime un fichier du cache (quand il est supprimé du projet)
        
        Args:
            file_path: Chemin du fichier
        """
        file_key = str(file_path)
        
        if file_key in self.cache["files"]:
            del self.cache["files"][file_key]
            print(f" Fichier retiré du cache : {file_path.name}")
    
    def get_file_dependencies(self, file_path: Path) -> Dict[str, list]:
        """
        Récupère les dépendances d'un fichier depuis le cache
        
        Args:
            file_path: Chemin du fichier
            
        Returns:
            Dict avec 'dependencies' et 'dependents'
        """
        file_key = str(file_path)
        
        if file_key not in self.cache["files"]:
            return {"dependencies": [], "dependents": []}
        
        file_data = self.cache["files"][file_key]
        
        return {
            "dependencies": file_data.get("dependencies", []),
            "dependents": file_data.get("dependents", [])
        }
    
    def update_dependencies(
        self,
        file_path: Path,
        dependencies: list,
        dependents: list
    ):
        """
        Met à jour uniquement les dépendances d'un fichier
        
        Args:
            file_path: Chemin du fichier
            dependencies: Nouvelles dépendances
            dependents: Nouveaux dépendants
        """
        file_key = str(file_path)
        
        if file_key in self.cache["files"]:
            self.cache["files"][file_key]["dependencies"] = dependencies
            self.cache["files"][file_key]["dependents"] = dependents
    
    def save(self):
        """Sauvegarde le cache sur disque"""
        try:
            with open(self.cache_file, 'wb') as f:
                pickle.dump(self.cache, f)
            
            # Aussi sauvegarder en JSON pour debug
            json_file = self.cache_dir / "analysis_cache.json"
            with open(json_file, 'w', encoding='utf-8') as f:
                # Convertir pour JSON (enlever les objets non sérialisables)
                cache_json = {
                    "metadata": self.cache["metadata"],
                    "files_count": len(self.cache["files"]),
                    "files": {
                        k: {
                            "hash": v["hash"][:16] + "...",
                            "last_modified": datetime.fromtimestamp(
                                v["last_modified"]
                            ).isoformat() if v.get("last_modified") else None,
                            "dependencies_count": len(v.get("dependencies", [])),
                            "dependents_count": len(v.get("dependents", []))
                        }
                        for k, v in list(self.cache["files"].items())[:10]  # Premiers 10
                    }
                }
                json.dump(cache_json, f, indent=2)
            
        except Exception as e:
            print(f"  Erreur sauvegarde cache : {e}")
    
    def load(self):
        """Charge le cache depuis le disque"""
        if not self.cache_file.exists():
            print(" Pas de cache existant, création d'un nouveau cache")
            return
        
        try:
            with open(self.cache_file, 'rb') as f:
                self.cache = pickle.load(f)
            
            files_count = len(self.cache.get("files", {}))
            print(f" Cache chargé : {files_count} fichiers en cache")
        
        except Exception as e:
            print(f" Erreur chargement cache : {e}")
            print("  Création d'un nouveau cache")
            self.cache = {
                "files": {},
                "metadata": {
                    "version": "1.0",
                    "last_updated": None
                }
            }
    
    def clear(self):
        """Efface tout le cache"""
        self.cache = {
            "files": {},
            "metadata": {
                "version": "1.0",
                "last_updated": None
            }
        }
        
        if self.cache_file.exists():
            self.cache_file.unlink()
        
        print("  Cache effacé")
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du cache"""
        files_data = self.cache.get("files", {})
        
        total_files = len(files_data)
        total_dependencies = sum(
            len(f.get("dependencies", [])) for f in files_data.values()
        )
        
        last_updated = self.cache["metadata"].get("last_updated")
        last_updated_str = None
        if last_updated:
            last_updated_str = datetime.fromtimestamp(last_updated).isoformat()
        
        return {
            "total_files": total_files,
            "total_dependencies": total_dependencies,
            "last_updated": last_updated_str,
            "cache_file": str(self.cache_file),
            "cache_size_bytes": self.cache_file.stat().st_size if self.cache_file.exists() else 0
        }
    
    def print_stats(self):
        """Affiche les statistiques du cache"""
        stats = self.get_stats()
        
       
        print(f" STATISTIQUES DU CACHE")
    
        print(f"Fichiers en cache    : {stats['total_files']}")
        print(f"Dépendances totales  : {stats['total_dependencies']}")
        print(f"Dernière MAJ         : {stats['last_updated'] or 'Jamais'}")
        print(f"Taille du cache      : {stats['cache_size_bytes'] / 1024:.2f} KB")
        print(f"Fichier cache        : {stats['cache_file']}")
    


# Exemple d'utilisation
if __name__ == "__main__":
    # Créer le gestionnaire de cache
    cache = CacheManager()
    
    # Afficher les stats
    cache.print_stats()
    
    # Test avec un fichier
    test_file = Path(__file__)
    
    print(f"\n Test avec {test_file.name}:")
    
    # Vérifier si le fichier a changé
    has_changed = cache.has_file_changed(test_file)
    print(f"  Fichier modifié ? {has_changed}")
    
    # Calculer le hash
    file_hash = cache.compute_file_hash(test_file)
    print(f"  Hash : {file_hash[:16]}...")
    
    # Mettre à jour le cache avec une fausse analyse
    cache.update_file_cache(
        file_path=test_file,
        analysis={
            "analysis": "Test analysis",
            "relevant_knowledge": [],
            "context": {}
        },
        dependencies=["config.py"],
        dependents=[]
    )
    
    # Re-vérifier
    has_changed = cache.has_file_changed(test_file)
    print(f"  Fichier modifié après cache ? {has_changed}")
    
    # Sauvegarder
    cache.save()
    print(f"\n Cache sauvegardé")
    
    # Afficher les stats finales
    cache.print_stats()