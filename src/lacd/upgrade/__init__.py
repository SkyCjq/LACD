"""LACD upgrade subsystem: release discovery, compatibility, migration, planning.

Submodules:
- source:  trusted release sources (GitHub Releases / local file)
- compat:  SemVer compatibility policy and support-window checks
- migrate: three-way migration engine with backup and rollback
- planner: orchestration that assembles a MigrationPlan
"""
