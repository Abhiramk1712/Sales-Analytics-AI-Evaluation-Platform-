"""
tests/test_manifest_schema.py
=============================
Tests for manifest contract layer, relationship validation, and drift detection.
"""

import pytest
from datetime import datetime
from backend.ingestion.manifest_schema import (
    ManifestSchema,
    TableSpec,
    ColumnMapping,
    ColumnType,
    RelationshipSpec,
    DirectForeignKey,
    BusinessKeyMapping,
    RequiredLevel,
    RelationshipType,
    ManifestDriftDetector,
)
from backend.ingestion.relationship_validator import RelationshipValidator
from backend.ingestion.source_registry import get_manifest_registry


class TestColumnMapping:
    """Test column mapping specifications."""
    
    def test_column_mapping_basic(self):
        col = ColumnMapping(
            source_name="User_ID",
            target_name="user_id",
            target_type=ColumnType.STRING,
            nullable=False,
            synonyms=["uid", "UserID"]
        )
        assert col.source_name == "User_ID"
        assert col.target_name == "user_id"
        assert col.target_type == ColumnType.STRING
        assert col.mapping_confidence == 1.0
    
    def test_column_mapping_with_transform(self):
        col = ColumnMapping(
            source_name="created_date",
            target_name="created_date",
            target_type=ColumnType.DATE,
            nullable=False,
            transform="date_parse"
        )
        assert col.transform == "date_parse"


class TestTableSpec:
    """Test table specifications."""
    
    def test_table_spec_creation(self):
        table = TableSpec(
            table_name="users",
            description="Sales representatives",
            required_level=RequiredLevel.HARD_REQUIRED,
            columns=[
                ColumnMapping(
                    source_name="User_ID",
                    target_name="id",
                    target_type=ColumnType.STRING,
                    nullable=False
                ),
                ColumnMapping(
                    source_name="Email",
                    target_name="email",
                    target_type=ColumnType.STRING,
                    nullable=False
                )
            ],
            primary_key=["id"],
            business_keys=[["email"]]
        )
        assert table.table_name == "users"
        assert len(table.columns) == 2
        assert table.required_level == RequiredLevel.HARD_REQUIRED
    
    def test_get_column(self):
        table = TableSpec(
            table_name="users",
            columns=[
                ColumnMapping(
                    source_name="User_ID",
                    target_name="id",
                    target_type=ColumnType.STRING,
                    nullable=False
                )
            ]
        )
        col = table.get_column("id")
        assert col is not None
        assert col.target_name == "id"
    
    def test_get_required_columns(self):
        table = TableSpec(
            table_name="users",
            columns=[
                ColumnMapping(
                    source_name="User_ID",
                    target_name="id",
                    target_type=ColumnType.STRING,
                    nullable=False
                ),
                ColumnMapping(
                    source_name="Email",
                    target_name="email",
                    target_type=ColumnType.STRING,
                    nullable=True
                )
            ]
        )
        required = table.get_required_columns()
        assert required == ["id"]


class TestRelationshipSpec:
    """Test relationship specifications."""
    
    def test_direct_fk_relationship(self):
        rel = RelationshipSpec(
            relationship_id="users_team_fk",
            relationship_type=RelationshipType.DIRECT_FK,
            direct_fk=DirectForeignKey(
                local_table="users",
                local_column="team_id",
                remote_table="teams",
                remote_column="id",
                nullable=True
            )
        )
        assert rel.relationship_id == "users_team_fk"
        assert rel.direct_fk.local_table == "users"
    
    def test_business_key_relationship(self):
        rel = RelationshipSpec(
            relationship_id="users_email_lookup",
            relationship_type=RelationshipType.BUSINESS_KEY,
            business_key=BusinessKeyMapping(
                local_table="opportunities",
                local_columns=["owner_email"],
                remote_table="users",
                remote_columns=["email"],
                match_type="case_insensitive"
            )
        )
        assert rel.relationship_type == RelationshipType.BUSINESS_KEY
        assert rel.business_key.local_columns == ["owner_email"]


class TestManifestSchema:
    """Test complete manifest schema."""
    
    def test_manifest_creation(self):
        manifest = ManifestSchema(
            version="1.0.0",
            name="sales_schema"
        )
        assert manifest.version == "1.0.0"
        assert manifest.name == "sales_schema"
        assert len(manifest.tables) == 0
    
    def test_manifest_add_table(self):
        manifest = ManifestSchema(
            version="1.0.0",
            name="sales_schema"
        )
        table = TableSpec(
            table_name="users",
            required_level=RequiredLevel.HARD_REQUIRED,
            columns=[
                ColumnMapping(
                    source_name="User_ID",
                    target_name="id",
                    target_type=ColumnType.STRING,
                    nullable=False
                )
            ]
        )
        manifest.add_table(table)
        assert "users" in manifest.tables
        assert manifest.get_table("users") is not None
    
    def test_get_hard_required_tables(self):
        manifest = ManifestSchema(
            version="1.0.0",
            name="sales_schema"
        )
        manifest.add_table(TableSpec(
            table_name="users",
            required_level=RequiredLevel.HARD_REQUIRED
        ))
        manifest.add_table(TableSpec(
            table_name="activities",
            required_level=RequiredLevel.OPTIONAL
        ))
        required = manifest.get_hard_required_tables()
        assert "users" in required
        assert "activities" not in required


class TestRelationshipValidator:
    """Test relationship validation and cycle detection."""
    
    def test_valid_manifest_no_cycles(self):
        """Test validation of a manifest with no cycles."""
        manifest = ManifestSchema(version="1.0.0", name="test")
        
        # Add tables
        manifest.add_table(TableSpec(
            table_name="teams",
            required_level=RequiredLevel.HARD_REQUIRED,
            columns=[
                ColumnMapping(
                    source_name="id",
                    target_name="id",
                    target_type=ColumnType.STRING,
                    nullable=False
                )
            ],
            primary_key=["id"]
        ))
        manifest.add_table(TableSpec(
            table_name="users",
            required_level=RequiredLevel.HARD_REQUIRED,
            columns=[
                ColumnMapping(
                    source_name="id",
                    target_name="id",
                    target_type=ColumnType.STRING,
                    nullable=False
                ),
                ColumnMapping(
                    source_name="team_id",
                    target_name="team_id",
                    target_type=ColumnType.STRING,
                    nullable=True
                )
            ],
            primary_key=["id"]
        ))
        
        # Add relationship: users -> teams
        manifest.add_relationship(RelationshipSpec(
            relationship_id="users_team_fk",
            relationship_type=RelationshipType.DIRECT_FK,
            direct_fk=DirectForeignKey(
                local_table="users",
                local_column="team_id",
                remote_table="teams",
                remote_column="id",
                nullable=True
            ),
            is_required=True
        ))
        
        # Validate
        validator = RelationshipValidator(manifest)
        assert validator.validate_all() is True
    
    def test_load_order_computation(self):
        """Test topological sort for load order."""
        manifest = ManifestSchema(version="1.0.0", name="test")
        
        # teams (no deps) -> users (depends on teams)
        manifest.add_table(TableSpec(table_name="teams", columns=[]))
        manifest.add_table(TableSpec(table_name="users", columns=[
            ColumnMapping(
                source_name="team_id",
                target_name="team_id",
                target_type=ColumnType.STRING,
                nullable=True
            )
        ]))
        
        manifest.add_relationship(RelationshipSpec(
            relationship_id="users_team_fk",
            relationship_type=RelationshipType.DIRECT_FK,
            direct_fk=DirectForeignKey(
                local_table="users",
                local_column="team_id",
                remote_table="teams",
                remote_column="id"
            )
        ))
        
        validator = RelationshipValidator(manifest)
        load_order = validator.get_load_order()
        
        assert load_order is not None
        # teams should come before users
        assert load_order.index("teams") < load_order.index("users")


class TestManifestDriftDetector:
    """Test schema drift detection."""
    
    def test_detect_column_drift_exact_match(self):
        """Test drift detection with exact column match."""
        manifest_cols = [
            ColumnMapping(
                source_name="id",
                target_name="user_id",
                target_type=ColumnType.STRING,
                nullable=False
            ),
            ColumnMapping(
                source_name="email",
                target_name="email",
                target_type=ColumnType.STRING,
                nullable=False
            )
        ]
        actual_cols = ["user_id", "email"]
        
        drift = ManifestDriftDetector.detect_column_drift(manifest_cols, actual_cols)
        
        assert drift["drift_detected"] is False
        assert drift["missing_in_actual"] == []
        assert drift["extra_in_actual"] == []
    
    def test_detect_column_drift_missing_columns(self):
        """Test drift detection with missing columns."""
        manifest_cols = [
            ColumnMapping(
                source_name="id",
                target_name="user_id",
                target_type=ColumnType.STRING,
                nullable=False
            ),
            ColumnMapping(
                source_name="email",
                target_name="email",
                target_type=ColumnType.STRING,
                nullable=False
            )
        ]
        actual_cols = ["user_id"]
        
        drift = ManifestDriftDetector.detect_column_drift(manifest_cols, actual_cols)
        
        assert drift["drift_detected"] is True
        assert "email" in drift["missing_in_actual"]
    
    def test_compute_fingerprint(self):
        """Test fingerprint computation."""
        manifest = ManifestSchema(
            version="1.0.0",
            name="test"
        )
        manifest.add_table(TableSpec(
            table_name="users",
            columns=[
                ColumnMapping(
                    source_name="id",
                    target_name="id",
                    target_type=ColumnType.STRING,
                    nullable=False
                )
            ]
        ))
        
        fingerprint = ManifestDriftDetector.compute_fingerprint(manifest)
        assert isinstance(fingerprint, str)
        assert len(fingerprint) == 64  # SHA256 hex digest length


class TestManifestRegistry:
    """Test manifest registry and loading."""
    
    def test_load_manifest_from_disk(self):
        """Test loading manifest from disk."""
        registry = get_manifest_registry()
        manifest = registry.load_manifest("sales_schema", "v1")
        
        assert manifest.name == "sales_schema"
        assert manifest.version == "1.0.0"
        assert "users" in manifest.tables
    
    def test_manifest_validation_report(self):
        """Test full validation report."""
        registry = get_manifest_registry()
        report = registry.validate_manifest("sales_schema", "v1")
        
        assert isinstance(report, dict)
        assert "valid" in report
        assert "errors" in report
        assert "load_order" in report
        assert "tables_count" in report
        assert "relationships_count" in report
    
    def test_list_manifests(self):
        """Test listing available manifests."""
        registry = get_manifest_registry()
        manifests = registry.list_manifests()
        
        assert isinstance(manifests, list)
        assert len(manifests) > 0
        # Should include sales_schema_v1
        names = [m["name"] for m in manifests]
        assert "sales_schema" in names
