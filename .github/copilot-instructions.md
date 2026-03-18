# Copilot Workspace Instructions for VG Test

## Purpose
This workspace contains a Power BI/Fabric project with custom CI/CD, semantic model (TMDL), and report (legacy JSON) validation. These instructions guide Copilot agents to follow project-specific conventions, avoid common pitfalls, and use the correct build/test commands.

---

## Build & Test Commands
- **Semantic Model Validation:**
  - `TabularEditor.exe ./Games.SemanticModel/definition --validate`
  - `TabularEditor.exe ./Games.SemanticModel/definition -A ./bpa-rules/BPARules.json`
- **Report BPA Validation:**
  - `python bpa-rules/validate_report_bpa.py "Games Report.Report/report.json"`

---

## Architecture & Component Boundaries
- **Semantic Model:** TMDL files in `Games.SemanticModel/definition/`
- **Report:** Legacy JSON in `Games Report.Report/`
- **Model BPA:** C# LINQ rules in `bpa-rules/BPARules.json`
- **Report BPA:** Python rules in `bpa-rules/ReportBPARules.json` + `validate_report_bpa.py`

---

## CI/CD Workflow Notes
- `.github/workflows/semantic-model-validation.yml` is the **semantic model PR quality gate**
- `.github/workflows/validate-report-bpa.yml` is the **report PR quality gate**
- `.github/workflows/sync-to-fabric.yml` is the **deployment workflow**: it syncs Git → Fabric using the Fabric `updateFromGit` API
- `sync-to-fabric.yml` runs on **push to `main`** when files under `Games.SemanticModel/**` or `Games Report.Report/**` change, and on `workflow_dispatch`
- Changes only under `.github/workflows/**` do **not** trigger `sync-to-fabric.yml`; use manual dispatch when testing workflow-only changes
- Keep validation separate from deployment: PRs should pass the validation workflows before merge
- The sync workflow includes pre-flight checks for both semantic model structure and report structure before calling Fabric APIs

---

## Project-Specific Conventions
- Use `/// text` doc comments for descriptions (columns, measures, tables)
- **Never** use `description:` property on columns (Fabric parser rejects)
- Hide FK columns with `isHidden: true`
- Place annotations at root level in `model.tmdl`
- Use fully qualified DAX references: `Games[Column]`
- Use `DIVIDE()` instead of `/` in DAX
- **Report JSON:** Use old format (`report.json` at root), not PBIR
- Visual queries: use `prototypeQuery.From` with short aliases and `NativeReferenceName`
- Config fields must be stringified JSON

---

## Required Files (Do Not Delete)
- `Games.SemanticModel/definition.pbism` (must have `version` field)
- `Games.SemanticModel/definition/database.tmdl` (must have `compatibilityMode: powerBI`, `language: 1033`)
- `Games.SemanticModel/definition/model.tmdl` (must include `ref table` for each table)
- `.platform` (Fabric logicalId linkage)

---

## Common Pitfalls & Environment Issues
- Missing `definition.pbism` or `language:` in `database.tmdl` causes parse failures
- Using `description:` on columns causes sync errors
- Annotations inside model block (should be root level)
- PBIR format for report causes endless loading in Fabric
- Wrong query format or source refs in report JSON break visuals
- Model validation requires Windows (Tabular Editor .NET)
- Report BPA requires Python 3.12 (Ubuntu)
- BPA severity 2+ blocks merge

---

## Key Files
- `docs/fabric-cicd-guide.md`: Runbook, architecture, schema, DAX, CI/CD gotchas
- `Games.SemanticModel/definition/database.tmdl`: Fabric compatibility template
- `Games.SemanticModel/definition/tables/Games.tmdl`: Doc comments, hidden FK, DAX
- `.github/workflows/semantic-model-validation.yml`: Tabular Editor + BPA pattern
- `bpa-rules/validate_report_bpa.py`: Python JSON validation + rule eval

---

## Example Prompts
- "Validate the semantic model and report for CI/CD."
- "Add a new measure to Games.tmdl with a doc comment."
- "Update the report JSON to use prototypeQuery.From with short aliases."
- "Check for forbidden description: properties in TMDL columns."

---

## Next Steps
- Consider agent customizations for:
  - TMDL linting and doc comment enforcement
  - Automated report JSON migration/validation
  - CI/CD troubleshooting assistant

See `docs/fabric-cicd-guide.md` for deep dives and troubleshooting.
