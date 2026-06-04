"""
app/teams/__init__.py
─────────────────────
Each team lives in its own sub-package under app/teams/.

To add a new team:
  1. Create app/teams/<new_team>/ with team.py exporting a TeamContract
     subclass and graph.py / formulas.py / report.py as needed.
  2. Register it in app/shared/registry.py.
"""
