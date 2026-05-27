"""
backend/ingestion/source_registry.py
====================================
Registry for data sources (files, APIs, databases, etc.)
Also provides manifest registry for loading and managing schema contracts.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional, Dict, Any, List
from pathlib import Path
import json

from .manifest_schema import ManifestSchema, ManifestDriftDetector
from .relationship_validator import RelationshipValidator


@dataclass
class DataSource:
    """Represents a registered data source."""
    
    name: str
    source_type: str  # 'csv', 'api', 'database', 'parquet', etc.
    location: str     # file path, URL, or connection string
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    registered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    
    def __repr__(self) -> str:
        return f"DataSource(name={self.name}, type={self.source_type}, location={self.location})"


class SourceRegistry:
    """
    Registry for managing data sources.
    
    Allows registration and lookup of data sources (CSV files, APIs, etc.)
    """
    
    def __init__(self):
        """Initialize empty registry."""
        self._sources: Dict[str, DataSource] = {}
    
    def register(
        self,
        name: str,
        source_type: str,
        location: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DataSource:
        """
        Register a new data source.
        
        Args:
            name: Unique source name
            source_type: Type of source ('csv', 'api', 'database', etc.)
            location: File path, URL, or connection string
            description: Optional description
            metadata: Optional key-value metadata
        
        Returns:
            Registered DataSource object
        """
        if name in self._sources:
            raise ValueError(f"Source '{name}' already registered")
        
        source = DataSource(
            name=name,
            source_type=source_type,
            location=location,
            description=description,
            metadata=metadata or {},
        )
        self._sources[name] = source
        return source
    
    def get(self, name: str) -> Optional[DataSource]:
        """Retrieve a registered source by name."""
        return self._sources.get(name)
    
    def list_sources(self) -> list[DataSource]:
        """List all registered sources."""
        return list(self._sources.values())
    
    def unregister(self, name: str) -> bool:
        """Unregister a source. Returns True if successful."""
        if name in self._sources:
            del self._sources[name]
            return True
        return False


# Global registry instance
_global_registry = SourceRegistry()


def get_registry() -> SourceRegistry:
    """Get the global source registry."""
    return _global_registry


class ManifestRegistry:
    """
    Registry and loader for manifest schemas.
    
    Manages manifest versions, drift detection, and provides
    validation utilities for schema contracts.
    """
    
    def __init__(self, manifest_dir: Optional[Path] = None):
        """
        Initialize manifest registry.
        
        Args:
            manifest_dir: Directory containing manifest JSON files.
                         Defaults to backend/ingestion/manifests/
        """
        self.manifest_dir = manifest_dir or Path(__file__).parent / "manifests"
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        self._manifests: Dict[str, ManifestSchema] = {}
        self._validators: Dict[str, RelationshipValidator] = {}
    
    def load_manifest(self, manifest_name: str, version: str = "v1") -> ManifestSchema:
        """
        Load a manifest from disk or cache.
        
        Args:
            manifest_name: Name of the manifest (e.g., 'sales_schema')
            version: Version identifier (e.g., 'v1', 'v2')
        
        Returns:
            ManifestSchema object
        
        Raises:
            FileNotFoundError: If manifest file not found
            ValueError: If manifest JSON is invalid
        """
        cache_key = f"{manifest_name}:{version}"
        
        if cache_key in self._manifests:
            return self._manifests[cache_key]
        
        # Build path: manifests/{manifest_name}_{version}.json
        manifest_path = self.manifest_dir / f"{manifest_name}_{version}.json"
        
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Manifest not found: {manifest_path}. "
                f"Available manifests: {list(self.manifest_dir.glob('*.json'))}"
            )
        
        # Load and parse JSON
        with open(manifest_path, 'r') as f:
            manifest_data = json.load(f)
        
        # Parse into ManifestSchema (Pydantic will validate)
        try:
            manifest = ManifestSchema(**manifest_data)
        except Exception as e:
            raise ValueError(f"Failed to parse manifest {manifest_path}: {e}")
        
        # Compute fingerprint for drift detection
        manifest.schema_fingerprint = ManifestDriftDetector.compute_fingerprint(manifest)
        
        # Cache
        self._manifests[cache_key] = manifest
        
        return manifest
    
    def register_manifest(self, manifest: ManifestSchema, save: bool = True) -> None:
        """
        Register a manifest in memory and optionally save to disk.
        
        Args:
            manifest: ManifestSchema object
            save: Whether to save to disk
        """
        cache_key = f"{manifest.name}:{manifest.version}"
        self._manifests[cache_key] = manifest
        
        if save:
            manifest_path = self.manifest_dir / f"{manifest.name}_{manifest.version}.json"
            with open(manifest_path, 'w') as f:
                json.dump(manifest.model_dump(), f, indent=2, default=str)
    
    def get_validator(self, manifest_name: str, version: str = "v1") -> RelationshipValidator:
        """
        Get or create a validator for a manifest.
        
        Args:
            manifest_name: Name of the manifest
            version: Version identifier
        
        Returns:
            RelationshipValidator instance
        """
        cache_key = f"{manifest_name}:{version}"
        
        if cache_key in self._validators:
            return self._validators[cache_key]
        
        manifest = self.load_manifest(manifest_name, version)
        validator = RelationshipValidator(manifest)
        self._validators[cache_key] = validator
        
        return validator
    
    def validate_manifest(self, manifest_name: str, version: str = "v1") -> Dict[str, Any]:
        """
        Validate a manifest and return full validation report.
        
        Args:
            manifest_name: Name of the manifest
            version: Version identifier
        
        Returns:
            Validation report dictionary
        """
        validator = self.get_validator(manifest_name, version)
        validator.validate_all()
        return validator.get_validation_report()
    
    def list_manifests(self) -> List[Dict[str, str]]:
        """List all available manifests."""
        manifests = []
        for json_file in self.manifest_dir.glob('*.json'):
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    manifests.append({
                        'name': data.get('name'),
                        'version': data.get('version'),
                        'file': json_file.name,
                    })
            except Exception:
                pass
        return sorted(manifests, key=lambda x: (x['name'], x['version']))
    
    def detect_schema_drift(
        self,
        manifest_name: str,
        actual_columns: Dict[str, List[str]],
        version: str = "v1"
    ) -> Dict[str, Any]:
        """
        Detect schema drift between manifest and actual data.
        
        Args:
            manifest_name: Name of the manifest
            actual_columns: Dict mapping table_name -> list of actual column names
            version: Version identifier
        
        Returns:
            Drift detection report
        """
        manifest = self.load_manifest(manifest_name, version)
        report = {}
        
        for table_name, actual_cols in actual_columns.items():
            table = manifest.get_table(table_name)
            if not table:
                report[table_name] = {'error': f'Table {table_name} not in manifest'}
                continue
            
            drift = ManifestDriftDetector.detect_column_drift(
                table.columns,
                actual_cols
            )
            report[table_name] = drift
        
        return report


# Global manifest registry instance
_global_manifest_registry = ManifestRegistry()


def get_manifest_registry() -> ManifestRegistry:
    """Get the global manifest registry."""
    return _global_manifest_registry
