"""
backend/ingestion/relationship_validator.py
===========================================
Validates manifest relationships: cycle detection, topological sorting, and dependency analysis.
"""

from typing import Dict, List, Set, Tuple, Optional, Any
from collections import defaultdict, deque
from .manifest_schema import (
    ManifestSchema,
    RelationshipSpec,
    RelationshipType,
    DirectForeignKey,
    BusinessKeyMapping,
)


class RelationshipValidator:
    """
    Validates and analyzes relationships in a manifest.
    
    - Detects circular dependencies (cycles)
    - Computes topological order for load sequences
    - Validates relationship integrity
    - Reports missing referenced tables
    """
    
    def __init__(self, manifest: ManifestSchema):
        """Initialize validator with a manifest."""
        self.manifest = manifest
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def validate_all(self) -> bool:
        """
        Run all validations. Returns True if valid, False if errors found.
        
        Checks:
        1. All referenced tables exist
        2. No circular dependencies
        3. All columns referenced exist
        4. Required relationships are consistent
        """
        self.errors = []
        self.warnings = []
        
        # Check 1: Referenced tables exist
        self._validate_table_references()
        
        # Check 2: No cycles
        self._validate_no_cycles()
        
        # Check 3: Columns exist
        self._validate_column_references()
        
        # Check 4: Required tables are present
        self._validate_required_tables()
        
        return len(self.errors) == 0
    
    def _validate_table_references(self) -> None:
        """Ensure all referenced tables exist in manifest."""
        table_names = set(self.manifest.tables.keys())
        
        for rel_id, rel in self.manifest.relationships.items():
            tables_in_rel = set()
            
            if rel.direct_fk:
                tables_in_rel.add(rel.direct_fk.local_table)
                tables_in_rel.add(rel.direct_fk.remote_table)
            elif rel.business_key:
                tables_in_rel.add(rel.business_key.local_table)
                tables_in_rel.add(rel.business_key.remote_table)
            elif rel.code_mapping:
                tables_in_rel.add(rel.code_mapping.local_table)
                tables_in_rel.add(rel.code_mapping.remote_table)
            
            missing = tables_in_rel - table_names
            if missing:
                for table in missing:
                    self.errors.append(
                        f"Relationship {rel_id}: references undefined table '{table}'"
                    )
    
    def _validate_no_cycles(self) -> None:
        """Detect circular dependencies using DFS."""
        # Build adjacency list (directed graph)
        graph = self._build_dependency_graph()
        
        # Check for cycles in the graph
        visited = set()
        rec_stack = set()
        cycle_path = []
        
        def has_cycle(node: str, path: List[str]) -> bool:
            """DFS to detect cycles."""
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor, path):
                        return True
                elif neighbor in rec_stack:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    self.errors.append(f"Circular dependency detected: {' -> '.join(cycle)}")
                    return True
            
            rec_stack.remove(node)
            path.pop()
            return False
        
        # Check all tables
        for table_name in self.manifest.tables.keys():
            if table_name not in visited:
                has_cycle(table_name, [])
    
    def _build_dependency_graph(self) -> Dict[str, List[str]]:
        """
        Build directed graph of table dependencies.
        Edge table_a -> table_b means table_b must be loaded before table_a.
        """
        graph = defaultdict(list)
        
        for rel in self.manifest.relationships.values():
            if rel.direct_fk:
                fk = rel.direct_fk
                # local depends on remote
                if fk.on_delete != "cascade":
                    graph[fk.local_table].append(fk.remote_table)
            
            elif rel.business_key:
                bk = rel.business_key
                # local depends on remote for matching
                graph[bk.local_table].append(bk.remote_table)
            
            elif rel.code_mapping:
                cm = rel.code_mapping
                # local depends on remote for code translation
                graph[cm.local_table].append(cm.remote_table)
        
        return dict(graph)
    
    def _validate_column_references(self) -> None:
        """Ensure all columns referenced in relationships exist."""
        for rel_id, rel in self.manifest.relationships.items():
            if rel.direct_fk:
                fk = rel.direct_fk
                # Check local table has the local column
                local_table = self.manifest.get_table(fk.local_table)
                if local_table and not local_table.get_column(fk.local_column):
                    self.errors.append(
                        f"Relationship {rel_id}: column '{fk.local_column}' not found in table '{fk.local_table}'"
                    )
                # Check remote table has the remote column
                remote_table = self.manifest.get_table(fk.remote_table)
                if remote_table and not remote_table.get_column(fk.remote_column):
                    self.errors.append(
                        f"Relationship {rel_id}: column '{fk.remote_column}' not found in table '{fk.remote_table}'"
                    )
            
            elif rel.business_key:
                bk = rel.business_key
                # Check all local columns exist
                local_table = self.manifest.get_table(bk.local_table)
                if local_table:
                    for col in bk.local_columns:
                        if not local_table.get_column(col):
                            self.errors.append(
                                f"Relationship {rel_id}: column '{col}' not found in table '{bk.local_table}'"
                            )
                # Check all remote columns exist
                remote_table = self.manifest.get_table(bk.remote_table)
                if remote_table:
                    for col in bk.remote_columns:
                        if not remote_table.get_column(col):
                            self.errors.append(
                                f"Relationship {rel_id}: column '{col}' not found in table '{bk.remote_table}'"
                            )
            
            elif rel.code_mapping:
                cm = rel.code_mapping
                local_table = self.manifest.get_table(cm.local_table)
                if local_table and not local_table.get_column(cm.local_column):
                    self.errors.append(
                        f"Relationship {rel_id}: column '{cm.local_column}' not found in table '{cm.local_table}'"
                    )
                remote_table = self.manifest.get_table(cm.remote_table)
                if remote_table and not remote_table.get_column(cm.remote_column):
                    self.errors.append(
                        f"Relationship {rel_id}: column '{cm.remote_column}' not found in table '{cm.remote_table}'"
                    )
    
    def _validate_required_tables(self) -> None:
        """Check that hard-required tables are present."""
        hard_required = self.manifest.get_hard_required_tables()
        actual_tables = set(self.manifest.tables.keys())
        
        missing = set(hard_required) - actual_tables
        if missing:
            for table in missing:
                self.errors.append(
                    f"Hard-required table '{table}' is not defined in manifest"
                )
    
    def get_load_order(self) -> Optional[List[str]]:
        """
        Compute topological order for loading tables.
        Returns list of table names in dependency order, or None if cycles exist.
        
        Dependency direction: if users.team_id -> teams.id,
        then teams must be loaded before users (teams is a dependency of users).
        """
        graph = self._build_dependency_graph()
        
        # Build in-degree count: how many tables must be loaded before each table
        in_degree = {table: 0 for table in self.manifest.tables.keys()}
        
        # For each edge (table -> dependency), increment in-degree of table
        for table, dependencies in graph.items():
            in_degree[table] += len(dependencies)
        
        # Kahn's algorithm: start with tables that have no dependencies
        queue = deque([table for table, degree in in_degree.items() if degree == 0])
        result = []
        
        while queue:
            table = queue.popleft()
            result.append(table)
            
            # Find tables that depend on this table (need to reduce their in-degree)
            for other_table, deps in graph.items():
                if table in deps:
                    in_degree[other_table] -= 1
                    if in_degree[other_table] == 0:
                        queue.append(other_table)
        
        # Check if all tables were processed (no cycles)
        if len(result) != len(self.manifest.tables):
            return None  # Cycle detected
        
        return result
    
    def analyze_table_dependencies(self, table_name: str) -> Dict[str, Any]:
        """
        Analyze dependencies for a specific table.
        
        Returns:
            {
                'table': name,
                'depends_on': [table_names],
                'is_depended_on_by': [table_names],
                'required_tables': [table_names],
                'optional_tables': [table_names]
            }
        """
        graph = self._build_dependency_graph()
        
        depends_on = graph.get(table_name, [])
        
        # Find tables that depend on this one
        is_depended_on_by = [
            t for t, deps in graph.items() if table_name in deps
        ]
        
        # Classify as required/optional
        required_tables = [
            t for t in depends_on
            if (table := self.manifest.get_table(t)) and
            table.required_level.value == 'hard_required'
        ]
        optional_tables = [
            t for t in depends_on
            if (table := self.manifest.get_table(t)) and
            table.required_level.value == 'optional'
        ]
        
        return {
            'table': table_name,
            'depends_on': sorted(depends_on),
            'is_depended_on_by': sorted(is_depended_on_by),
            'required_tables': sorted(required_tables),
            'optional_tables': sorted(optional_tables),
        }
    
    def get_validation_report(self) -> Dict[str, Any]:
        """Get complete validation report."""
        return {
            'valid': len(self.errors) == 0,
            'errors': self.errors,
            'warnings': self.warnings,
            'load_order': self.get_load_order(),
            'tables_count': len(self.manifest.tables),
            'relationships_count': len(self.manifest.relationships),
            'hard_required_tables': self.manifest.get_hard_required_tables(),
            'optional_tables': self.manifest.get_optional_tables(),
        }
