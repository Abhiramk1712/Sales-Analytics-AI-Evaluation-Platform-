"""
backend/ingestion/manifest_schema.py
====================================
Manifest contract layer: Typed models for schema, relationships, and validation.

This module defines the canonical schema contract used by ingestion, generator,
validator, and agent. It transforms the user's JSON schema into a versioned,
enforceable contract with explicit typing, relationship validation, and drift detection.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Set, Any
from pydantic import BaseModel, Field, ConfigDict


class ColumnType(str, Enum):
    """Supported column types."""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    UUID = "uuid"
    DECIMAL = "decimal"


class RelationshipType(str, Enum):
    """Types of relationships between tables."""
    DIRECT_FK = "direct_fk"  # e.g., reps.team_id -> teams.id
    BUSINESS_KEY = "business_key"  # e.g., reps.email -> users.email
    CODE_MAPPING = "code_mapping"  # e.g., product_sku -> products.external_id
    MANY_TO_MANY = "many_to_many"  # e.g., users <-> territories via assignment table


class RequiredLevel(str, Enum):
    """Required level of data for operational continuity."""
    HARD_REQUIRED = "hard_required"  # Required for core product
    STRONGLY_RECOMMENDED = "strongly_recommended"  # Used by most features
    OPTIONAL = "optional"  # Nice-to-have; system has fallback


class ColumnMapping(BaseModel):
    """Maps a source column to a target column in the manifest."""
    
    model_config = ConfigDict(frozen=False)
    
    source_name: str = Field(..., description="Column name in source file")
    source_type: Optional[str] = Field(None, description="Inferred type from source data")
    target_name: str = Field(..., description="Canonical column name in manifest")
    target_type: ColumnType = Field(..., description="Expected type in canonical schema")
    nullable: bool = Field(False, description="Whether NULL values are allowed")
    synonyms: List[str] = Field(default_factory=list, description="Alternative source column names")
    transform: Optional[str] = Field(None, description="Transform function to apply (e.g., 'lower', 'date_parse')")
    mapping_confidence: float = Field(1.0, description="Confidence in this mapping (0.0-1.0)")
    

class TableSpec(BaseModel):
    """Specification for a table in the manifest."""
    
    model_config = ConfigDict(frozen=False)
    
    table_name: str = Field(..., description="Canonical table name")
    description: Optional[str] = Field(None, description="Human-readable table description")
    required_level: RequiredLevel = Field(RequiredLevel.OPTIONAL, description="Required level for operations")
    columns: List[ColumnMapping] = Field(default_factory=list, description="Column specifications")
    primary_key: List[str] = Field(default_factory=list, description="Primary key column names")
    business_keys: List[List[str]] = Field(default_factory=list, description="Business key columns (compound keys allowed)")
    indexes: List[List[str]] = Field(default_factory=list, description="Index column combinations")
    source_files: List[str] = Field(default_factory=list, description="Source file(s) providing data for this table")
    row_count: Optional[int] = Field(None, description="Expected row count (if known)")
    
    def get_column(self, name: str) -> Optional[ColumnMapping]:
        """Get column spec by target name."""
        for col in self.columns:
            if col.target_name == name:
                return col
        return None
    
    def get_required_columns(self) -> List[str]:
        """Get list of non-nullable column names."""
        return [col.target_name for col in self.columns if not col.nullable]


class DirectForeignKey(BaseModel):
    """Direct foreign key relationship: local_table.local_column -> remote_table.remote_column"""
    
    model_config = ConfigDict(frozen=False)
    
    local_table: str = Field(..., description="Source table name")
    local_column: str = Field(..., description="Column containing FK value")
    remote_table: str = Field(..., description="Referenced table name")
    remote_column: str = Field(..., description="Referenced column (usually PK)")
    nullable: bool = Field(False, description="Whether NULL values are allowed")
    on_delete: str = Field("restrict", description="Delete behavior: cascade, set_null, restrict")


class BusinessKeyMapping(BaseModel):
    """
    Business key mapping: match records via non-FK columns.
    Example: reps.email -> users.email
    """
    
    model_config = ConfigDict(frozen=False)
    
    local_table: str = Field(..., description="Source table name")
    local_columns: List[str] = Field(..., description="Columns that form the business key")
    remote_table: str = Field(..., description="Referenced table name")
    remote_columns: List[str] = Field(..., description="Columns in remote table forming the business key")
    match_type: str = Field("exact", description="Match type: exact, case_insensitive, email, etc.")
    fallback_column: Optional[str] = Field(None, description="Fallback column if primary business key fails")


class CodeMapping(BaseModel):
    """
    Code mapping: translate coded values from source to reference table.
    Example: product_category_code -> product_categories.code
    """
    
    model_config = ConfigDict(frozen=False)
    
    local_table: str = Field(..., description="Source table name")
    local_column: str = Field(..., description="Column with source codes")
    remote_table: str = Field(..., description="Reference table with canonical codes")
    remote_column: str = Field(..., description="Column containing canonical codes")
    value_column: Optional[str] = Field(None, description="Column to retrieve from reference table")


class RelationshipSpec(BaseModel):
    """Specification for relationships between tables."""
    
    model_config = ConfigDict(frozen=False)
    
    relationship_id: str = Field(..., description="Unique relationship identifier")
    relationship_type: RelationshipType = Field(..., description="Type of relationship")
    description: Optional[str] = Field(None, description="Human-readable description")
    
    # For different relationship types, store the spec
    direct_fk: Optional[DirectForeignKey] = Field(None, description="Direct FK spec (if type=direct_fk)")
    business_key: Optional[BusinessKeyMapping] = Field(None, description="Business key spec (if type=business_key)")
    code_mapping: Optional[CodeMapping] = Field(None, description="Code mapping spec (if type=code_mapping)")
    
    is_required: bool = Field(True, description="Whether this relationship is required for valid data")
    priority: int = Field(0, description="Load order priority (higher = earlier)")


class ManifestSchema(BaseModel):
    """Complete manifest schema contract."""
    
    model_config = ConfigDict(frozen=False)
    
    version: str = Field(..., description="Schema version (semantic: major.minor.patch)")
    name: str = Field(..., description="Manifest name (e.g., 'sales_schema')")
    description: Optional[str] = Field(None, description="Manifest description")
    created_at: datetime = Field(default_factory=datetime.now, description="When manifest was created")
    last_updated: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    tables: Dict[str, TableSpec] = Field(default_factory=dict, description="Table specifications by name")
    relationships: Dict[str, RelationshipSpec] = Field(default_factory=dict, description="Relationships by ID")
    
    required_tables: Set[str] = Field(default_factory=set, description="Tables required for core operations")
    optional_tables: Set[str] = Field(default_factory=set, description="Optional tables with fallback synthesis")
    
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Custom metadata")
    
    # Validation and drift detection
    schema_fingerprint: Optional[str] = Field(None, description="Hash of schema for drift detection")
    validation_rules: Dict[str, List[str]] = Field(default_factory=dict, description="Validation rules per table")
    
    def get_table(self, name: str) -> Optional[TableSpec]:
        """Get table spec by name."""
        return self.tables.get(name)
    
    def get_all_tables(self) -> List[TableSpec]:
        """Get all table specs."""
        return list(self.tables.values())
    
    def get_hard_required_tables(self) -> List[str]:
        """Get tables with HARD_REQUIRED level."""
        return [
            t.table_name for t in self.tables.values()
            if t.required_level == RequiredLevel.HARD_REQUIRED
        ]
    
    def get_optional_tables(self) -> List[str]:
        """Get tables with OPTIONAL level."""
        return [
            t.table_name for t in self.tables.values()
            if t.required_level == RequiredLevel.OPTIONAL
        ]
    
    def get_relationships_for_table(self, table_name: str) -> List[RelationshipSpec]:
        """Get all relationships involving a table."""
        result = []
        for rel in self.relationships.values():
            if rel.direct_fk and (rel.direct_fk.local_table == table_name or rel.direct_fk.remote_table == table_name):
                result.append(rel)
            elif rel.business_key and (rel.business_key.local_table == table_name or rel.business_key.remote_table == table_name):
                result.append(rel)
            elif rel.code_mapping and (rel.code_mapping.local_table == table_name or rel.code_mapping.remote_table == table_name):
                result.append(rel)
        return result
    
    def get_required_relationships(self) -> List[RelationshipSpec]:
        """Get all required relationships."""
        return [rel for rel in self.relationships.values() if rel.is_required]
    
    def add_table(self, table_spec: TableSpec) -> None:
        """Register a table spec."""
        self.tables[table_spec.table_name] = table_spec
    
    def add_relationship(self, rel_spec: RelationshipSpec) -> None:
        """Register a relationship spec."""
        self.relationships[rel_spec.relationship_id] = rel_spec


# Manifest drift detection utilities
class ManifestDriftDetector:
    """Detects schema drift between manifest and actual data."""
    
    @staticmethod
    def detect_column_drift(
        manifest_columns: List[ColumnMapping],
        actual_columns: List[str],
    ) -> Dict[str, Any]:
        """
        Detect if actual columns match manifest.
        
        Returns:
            {
                'manifest_columns': [names],
                'actual_columns': [names],
                'missing_in_actual': [names],
                'extra_in_actual': [names],
                'drift_detected': bool
            }
        """
        manifest_names = {col.target_name for col in manifest_columns}
        actual_set = set(actual_columns)
        
        missing = manifest_names - actual_set
        extra = actual_set - manifest_names
        
        return {
            'manifest_columns': sorted(manifest_names),
            'actual_columns': sorted(actual_set),
            'missing_in_actual': sorted(missing),
            'extra_in_actual': sorted(extra),
            'drift_detected': bool(missing or extra),
        }
    
    @staticmethod
    def compute_fingerprint(manifest: 'ManifestSchema') -> str:
        """Compute a hash of the manifest schema for drift detection."""
        import hashlib
        import json
        
        # Build a canonical JSON representation
        schema_dict = {
            'version': manifest.version,
            'tables': sorted([
                {
                    'name': t.table_name,
                    'columns': sorted([
                        {'name': c.target_name, 'type': c.target_type, 'nullable': c.nullable}
                        for c in t.columns
                    ], key=lambda x: x['name'])
                }
                for t in manifest.tables.values()
            ], key=lambda x: x['name'])
        }
        
        canonical_json = json.dumps(schema_dict, sort_keys=True)
        fingerprint = hashlib.sha256(canonical_json.encode()).hexdigest()
        return fingerprint
