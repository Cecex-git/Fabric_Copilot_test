# Fabric CI/CD Guide — GitHub → Microsoft Fabric Sync

This guide documents the setup, rules, and lessons learned for automatically syncing this Power BI semantic model from GitHub to the Microsoft Fabric `Copilot_Test` workspace.

---

## Architecture Overview

```
Pull Request → main
  └─ GitHub Actions: semantic-model-validation.yml
       ├─ Install Tabular Editor (portable)
       ├─ Validate TMDL folder loads correctly
       ├─ Run Best Practice Analyzer (BPA)
       ├─ Build & upload BPA report artifact
       └─ ❌ Block merge if violations found

Push to main (Games.SemanticModel/** or Games Report.Report/**)
  └─ GitHub Actions: sync-to-fabric.yml
       ├─ Pre-flight TMDL validation (definition.pbism, description:, database.tmdl)
       ├─ Pre-flight Report validation (.platform, definition.pbir, report.json)
       ├─ Entra ID token (Service Principal)
       ├─ Configure SP Git credentials (Fabric API)
       ├─ Check Git status (Fabric API)
       └─ updateFromGit → LRO polling → ✅ Fabric updated
```

The two workflows are complementary:
- **Validation** runs on every PR — catches quality issues before they reach `main`
- **Sync** runs after merge — deploys the validated model and report to Fabric

---

## Repository Structure

```
VG Test.pbip                        ← Power BI Project file — open this in Desktop

Games.SemanticModel/
  definition.pbism                  ← Required Fabric item descriptor
  .platform                         ← Fabric Git metadata (logicalId, type)
  definition/
    database.tmdl                   ← Compatibility + language settings
    model.tmdl                      ← Model-level metadata, relationships, annotations
    tables/
      Games.tmdl                    ← Fact table: columns, measures (Games Published), M query
      Platforms.tmdl                ← Dimension table: distinct platforms from CSV source

Games Report.Report/
  .platform                         ← Fabric Git metadata (logicalId, type: Report)
  definition.pbir                   ← Links the report to Games.SemanticModel
  report.json                       ← Report layout: pages, visuals, theme (Fabric-native format)
  StaticResources/
    SharedResources/
      BaseThemes/
        CY26SU02.json               ← Default Fabric theme file

.github/workflows/
  semantic-model-validation.yml    ← PR quality gate: TMDL load + model BPA
  validate-report-bpa.yml          ← PR quality gate: report visual BPA
  sync-to-fabric.yml               ← CI/CD sync workflow (push to main)

bpa-rules/
  BPARules.json                    ← Semantic model BPA rule definitions (Tabular Editor)
  ReportBPARules.json              ← Report visual BPA rule definitions (Python evaluator)
  validate_report_bpa.py           ← Python script that evaluates report BPA rules

docs/
  fabric-cicd-guide.md             ← This file
```

### `VG Test.pbip` — Power BI Project file

This is the entry point for **Power BI Desktop**. Double-clicking it opens Desktop with the Games semantic model loaded directly from the TMDL source files.

```json
{
  "version": "1.0",
  "artifacts": [{ "dataset": { "path": "Games.SemanticModel" } }],
  "settings": { "enableTmdlSerialization": true }
}
```

Key setting: `enableTmdlSerialization: true` tells Desktop to persist the model as TMDL files inside `Games.SemanticModel/definition/` rather than as a single binary blob. This is what makes the model human-readable and Git-diffable.

**Authoring workflow:**
1. Open `VG Test.pbip` in Power BI Desktop
2. Make changes to the model (columns, measures, M query, etc.)
3. Save in Desktop → TMDL files in `Games.SemanticModel/definition/` are updated on disk
4. Review the diff, open a PR → validation workflow runs
5. Merge → sync workflow deploys to Fabric

---

## Semantic Model

### Tables

| Table | Type | Source |
|---|---|---|
| `Games` | Fact | CSV — `https://raw.githubusercontent.com/yaylinda/nintendo-games-ratings/master/data.csv` |
| `Platforms` | Dimension | Derived — distinct `Platform` values from the same CSV |

### Relationships

| From | To | Cardinality | Active |
|---|---|---|---|
| `Games[Platform]` | `Platforms[Platform]` | Many-to-One | ✅ Yes |

> **BPA rule `LAYOUT_HIDE_FK_COLUMNS`:** The `Games[Platform]` FK column must be hidden (`isHidden: true` in TMDL) to avoid exposing it alongside the dimension table.

### Measures (`Games` table)

| Measure | DAX | Description |
|---|---|---|
| `Games Published` | `COUNTROWS(Games)` | Total number of games. Use with `Platforms[Platform]`, `Games[Genre]`, or `Games[Year of Release]` to group by dimension. |

---

## Games Report

The `Games Report.Report/` folder contains a Power BI report connected to the `Games` semantic model. It contains a single page ("Games by Platform") with a table visual showing `Platforms[Platform]` and `[Games Published]` sorted descending.

### ⚠️ Fabric Report Format — Important

Fabric Git integration does **not** use the newer PBIR format (the `definition/pages/*.json` / `definition/visuals/*.json` hierarchy). It uses the **old Power BI JSON format** with a single `report.json` at the report root.

**Fabric-native format (what works):**
```
Games Report.Report/
  .platform
  definition.pbir           ← dataset link only
  report.json               ← ALL report content: sections, visualContainers, config
  StaticResources/
    SharedResources/
      BaseThemes/<theme>.json
```

**PBIR format (does NOT render in Fabric Git integration):**
```
Games Report.Report/
  definition/
    version.json
    report.json
    pages/<page>/page.json
    pages/<page>/visuals/<guid>/visual.json
```

Although Fabric's `updateFromGit` API accepts PBIR files without error, the report will **not render** — the loading bar appears and never completes. Always use the old format (as exported from Fabric's own "Commit to Git" feature).

### `report.json` visual query format

In the old format, the visual query uses `prototypeQuery` with source aliases — **not** `queryState`:

```json
"prototypeQuery": {
  "Version": 2,
  "From": [
    { "Name": "p", "Entity": "Platforms", "Type": 0 },
    { "Name": "g", "Entity": "Games",    "Type": 0 }
  ],
  "Select": [
    {
      "Column":  { "Expression": { "SourceRef": { "Source": "p" } }, "Property": "Platform" },
      "Name": "Platforms.Platform", "NativeReferenceName": "Platform"
    },
    {
      "Measure": { "Expression": { "SourceRef": { "Source": "g" } }, "Property": "Games Published" },
      "Name": "Games.Games Published", "NativeReferenceName": "Games Published"
    }
  ],
  "OrderBy": [
    { "Direction": 2, "Expression": { "Measure": { "Expression": { "SourceRef": { "Source": "g" } }, "Property": "Games Published" } } }
  ]
}
```

Key points:
- `Source` refs use the short alias (`"p"`, `"g"`) defined in `From`, **not** `Entity` directly
- `NativeReferenceName` is the bare column/measure name without the table prefix
- Sort direction: `1` = Ascending, `2` = Descending
- The `config` and `visualContainers[].config` fields are **stringified JSON** (a JSON string containing another JSON object)

### Adding a new report page

To add a page, open the report in Power BI Desktop (via `VG Test.pbip`), add the page visually, save, then commit the resulting `report.json` changes. Do not hand-craft `report.json` — let Desktop generate it and export via Fabric's "Commit to Git" flow.

---

## Required Files — Do Not Delete

| File | Why it is required |
|---|---|
| `definition.pbism` | Fabric Git integration entry point. Without it every sync fails with `Required artifact is missing in 'definition.pbism'`. Content must be valid JSON with a `version` field. |
| `definition/database.tmdl` | Must contain `compatibilityMode: powerBI` and `language: 1033`. Missing either causes a silent parse failure in Fabric. |
| `definition/model.tmdl` | Must include a `ref table` entry for **every table** in the model (e.g. `ref table Games`, `ref table Platforms`) plus any root-level annotations. |
| `.platform` | Contains the Fabric `logicalId` that links the folder to the workspace item. |

### `definition.pbism` minimum content
```json
{"version":"4.0","settings":{}}
```

### `definition/database.tmdl` minimum content
```tmdl
database Games
    compatibilityLevel: 1700
    compatibilityMode: powerBI
    language: 1033
```

---

## TMDL Authoring Rules

These rules exist because **Fabric's TMDL parser is stricter than Tabular Editor 3**.

### ✅ Use doc comments (`///`) for descriptions
```tmdl
/// Title of the game
column Title
    dataType: string
    ...
```

### ❌ Do NOT use `description:` as a property on columns
```tmdl
column Title
    description: "Title of the game"   ← INVALID — Fabric parse error
```
The `description:` property is **not supported on column objects** by Fabric's parser. Use `/// text` doc comments instead. This syntax works for columns, measures, and tables.

### ✅ Keep annotations at root level in `model.tmdl`
```tmdl
annotation PBI_ProTooling = "...")   ← correct: root level

model Model                          ← annotation must NOT be inside this block
    ref table Games
```

### `dataType` note
`decimal` and `double` are distinct types in Fabric. If Tabular Editor exports `decimal` but Fabric shows `double`, align them manually to avoid silent type drift.

---

## Semantic Model Validation Workflow

**File:** `.github/workflows/semantic-model-validation.yml`

**Triggers:**
- Every pull request targeting `main`
- Manual dispatch (`workflow_dispatch`)

This workflow acts as a **quality gate** — it must pass before a PR can be merged. It runs on `windows-latest` because Tabular Editor is a Windows executable.

### Step summary

| Step | What it does |
|---|---|
| Checkout | Full history checkout (`fetch-depth: 0`) |
| Install Tabular Editor | Downloads the latest portable TE3 from GitHub Releases into `te/` |
| Validate TMDL folder | Runs `TabularEditor.exe ./Games.SemanticModel/definition --validate` — fails if TMDL cannot be parsed |
| Run BPA | Runs BPA using `bpa-rules/BPARules.json`; outputs raw results to `bpa-results/BPAConsole.txt` |
| Build BPA report | Parses raw output into a structured JSON report at `bpa-results/BPAFullReport.json` |
| Upload artifact | Uploads the `bpa-results/` folder as a downloadable artifact on every run |
| Fail on violations | Reads the report — exits with code 1 if any rule has status `Failed` or `Error` |

### BPA rules reference

Rules are defined in `bpa-rules/BPARules.json`. Each rule has a Severity (1 = low, 2 = medium, 3 = high).

#### DAX Expressions

| ID | Name | Severity |
|---|---|---|
| `DAX_COLUMNS_FULLY_QUALIFIED` | Column references should be fully qualified | 2 |
| `DAX_DIVISION_COLUMNS` | Avoid division (use DIVIDE function instead) | 3 |
| `DAX_MEASURES_UNQUALIFIED` | Measure references should be unqualified | 2 |
| `DAX_TODO` | Revisit TODO expressions | 1 |

#### Formatting

| ID | Name | Severity |
|---|---|---|
| `APPLY_FORMAT_STRING_MEASURES` | Provide format string for all visible measures | 3 |

#### Metadata

| ID | Name | Severity |
|---|---|---|
| `META_AVOID_FLOAT` | Do not use floating point data types | 3 |
| `META_SUMMARIZE_NONE` | Don't summarize numeric columns | 1 |

#### Model Layout

| ID | Name | Severity |
|---|---|---|
| `LAYOUT_ADD_TO_PERSPECTIVES` | Add objects to perspectives | 1 |
| `LAYOUT_COLUMNS_HIERARCHIES_DF` | Organize columns and hierarchies in display folders | 1 |
| `LAYOUT_HIDE_FK_COLUMNS` | Hide foreign key columns | 1 |
| `LAYOUT_LOCALIZE_DF` | Translate Display Folders | 1 |
| `LAYOUT_MEASURES_DF` | Organize measures in display folders | 1 |

#### Naming Conventions

| ID | Name | Severity |
|---|---|---|
| `NO_CAMELCASE_COLUMNS_HIERARCHIES` | Avoid CamelCase on visible columns and hierarchies | 3 |
| `NO_CAMELCASE_MEASURES_TABLES` | Avoid CamelCase on visible measures and tables | 3 |
| `RELATIONSHIP_COLUMN_NAMES` | Names of columns in relationships should be the same | 3 |
| `UPPERCASE_FIRST_LETTER_COLUMNS_HIERARCHIES` | Column and hierarchy names must start with uppercase letter | 3 |
| `UPPERCASE_FIRST_LETTER_MEASURES_TABLES` | Measure and table names must start with uppercase letter | 3 |

#### Performance

| ID | Name | Severity |
|---|---|---|
| `PERF_UNUSED_COLUMNS` | Remove unused columns | 2 |
| `PERF_UNUSED_MEASURES` | Remove unused measures | 1 |

### Reading the BPA report artifact

1. Open the failed PR in GitHub → **Actions** tab → select the run
2. Scroll to **Artifacts** → download `bpa-report`
3. Open `BPAFullReport.json` — each rule entry has:
   - `Status`: `Passed`, `Failed`, or `Error`
   - `Violations`: array of matching object names
   - `Errors`: array of rule evaluation errors

### Adding or modifying BPA rules

Edit `bpa-rules/BPARules.json` following the existing schema. Each rule requires:
- `ID` — unique string identifier
- `Name` — human-readable name (also used for violation matching in the report builder)
- `Category` — grouping label
- `Severity` — 1 (low), 2 (medium), 3 (high)
- `Scope` — comma-separated TE object types (e.g. `"Measure, DataColumn"`)
- `Expression` — C# LINQ expression evaluated by Tabular Editor

---

## Report Visual BPA Workflow

**File:** `.github/workflows/validate-report-bpa.yml`

**Triggers:**
- Every pull request targeting `main` with changes under `Games Report.Report/**`, `bpa-rules/ReportBPARules.json`, or `bpa-rules/validate_report_bpa.py`
- Manual dispatch (`workflow_dispatch`)

This workflow acts as a **report quality gate**, validating visual layout and accessibility before any change is merged. It runs `bpa-rules/validate_report_bpa.py` against `Games Report.Report/report.json`.

### Step summary

| Step | What it does |
|---|---|
| Checkout | Checks out repo files |
| Set up Python | Installs Python 3.12 |
| Run Report BPA | Runs `validate_report_bpa.py` — exits 1 if any Error or Warning violation found |

### BPA rules reference

Rules are defined in `bpa-rules/ReportBPARules.json`. Severity scale: 1 = Info, 2 = Warning, 3 = Error. The workflow **fails on any Error (3) or Warning (2)**. Info (1) violations are reported but do not fail CI.

#### Layout

| ID | Name | Severity |
|---|---|---|
| `REPORT_VISUAL_WITHIN_BOUNDS` | Visual must fit within page bounds | 3 (Error) |
| `REPORT_NO_OVERLAPPING_VISUALS` | Visuals must not overlap each other | 2 (Warning) |
| `REPORT_MAX_VISUALS_PER_PAGE` | Avoid too many visuals on a single page (max 6) | 1 (Info) |

#### Data

| ID | Name | Severity |
|---|---|---|
| `REPORT_VISUAL_HAS_PROJECTIONS` | Visual must have at least one field assigned | 3 (Error) |

#### Naming Conventions

| ID | Name | Severity |
|---|---|---|
| `REPORT_PAGE_HAS_DISPLAY_NAME` | Page must have a display name | 2 (Warning) |

#### Accessibility

| ID | Name | Severity |
|---|---|---|
| `REPORT_VISUAL_HAS_ALT_TEXT` | Visual must have alt text | 2 (Warning) |
| `REPORT_TEXT_SIZE_MIN_12PX` | Explicitly-set text sizes must be ≥ 12px | 2 (Warning) |

### Reading BPA output

When the workflow fails, open the run log and look for the `── ERRORS ──` and `── WARNINGS ──` sections. Each violation shows:
- The rule ID and name
- The page name
- The visual GUID (from `config.name`)
- A plain-language description of the violation

### Adding or modifying report BPA rules

Edit `bpa-rules/ReportBPARules.json`. Each rule requires:
- `ID` — unique string identifier
- `Name` — human-readable name
- `Category` — grouping label
- `Description` — explanation shown in workflow output
- `Severity` — 1 (Info), 2 (Warning), 3 (Error)
- `Enabled` — `true` or `false`

Rule-specific parameters (e.g. `MaxVisuals`, `MinTextSize`) are read by the evaluator script. To add a **new rule type** with custom logic, add the rule definition to `ReportBPARules.json` and add the corresponding evaluation block in `bpa-rules/validate_report_bpa.py`.

### Alt text requirement

Every visual must have alt text set. In the old Fabric `report.json` format, alt text is stored inside the stringified `config` JSON of a visual container:

```json
"singleVisual": {
  "objects": {
    "general": [{
      "properties": {
        "altText": {
          "expr": { "Literal": { "Value": "'Description of the visual'" } }
        }
      }
    }]
  }
}
```

The easiest way to set alt text is in Power BI Desktop: select the visual → **Format** pane → **General** → **Alt text**.

---

## GitHub Actions Workflow — Sync to Fabric

**File:** `.github/workflows/sync-to-fabric.yml`

**Triggers:**
- Push to `main` with changes under `Games.SemanticModel/**` or `Games Report.Report/**`
- Manual dispatch (`workflow_dispatch`)

### Step summary

| Step | What it does |
|---|---|
| Checkout | Checks out repo files (required for validation) |
| Validate TMDL structure | Pre-flight checks — see below. Fails fast before any API call. |
| Validate Report structure | Checks `.platform`, `definition.pbir`, and `report.json` are present and valid. |
| Get Entra token | Client-credentials flow using the Service Principal |
| Configure SP Git credentials | `PATCH /git/myGitCredentials` with `ConfiguredConnection` |
| Get Git status | Compares `workspaceHead` vs `remoteCommitHash` |
| updateFromGit | Syncs remote → workspace; polls LRO until `Succeeded` or `Failed` |

### Pre-flight validation checks

The **Validate TMDL structure** step runs before any Fabric API call. All checks are cumulative — every failure is reported before the step exits, so you can fix multiple issues in one commit.

#### Check 1 — `definition.pbism` present and valid

**What it checks:** the file exists and is valid JSON containing a `version` field.

**Why:** Fabric Git integration uses this file as the item descriptor. Without it every sync fails immediately with `Required artifact is missing in 'definition.pbism'`, regardless of whether the TMDL itself is valid.

**Expected output (pass):**
```
✅ Games.SemanticModel/definition.pbism present and valid
```

**Expected output (fail):**
```
❌ Missing Games.SemanticModel/definition.pbism — required by Fabric Git integration
```
or
```
❌ Games.SemanticModel/definition.pbism is invalid JSON or missing the 'version' field
```

**Fix:** restore the file with the minimum content:
```json
{"version":"4.0","settings":{}}
```

---

#### Check 2 — No `description:` properties in TMDL files

**What it checks:** scans all `.tmdl` files under `Games.SemanticModel/definition/` for lines matching `^\s+description:`.

**Why:** Fabric's TMDL parser does **not** support the `description:` property on column objects (and potentially other objects). Tabular Editor 3 accepts it silently, creating a false sense of safety. The error from Fabric is:
```
TMDL Format Error: Unsupported property - description is not a supported property in the current context!
```

**Expected output (pass):**
```
✅ No unsupported 'description:' properties found in TMDL files
```

**Expected output (fail):**
```
❌ Found 'description:' properties in TMDL — Fabric does not support this on columns.
   Use '/// text' doc comments instead:
   tables/Games.tmdl:23:    description: "Meta score"
```

**Fix:** replace `description: "text"` with a `/// text` doc comment on the line above the object declaration:
```tmdl
/// Meta score
column MetaScore
    dataType: int64
```

---

#### Check 3 — `database.tmdl` has required Fabric fields

**What it checks:**
- `database.tmdl` exists
- Contains `compatibilityMode: powerBI`
- Contains a `language:` field

**Why:** Without `compatibilityMode: powerBI`, Fabric fails to parse the model silently. Without `language:`, locale-dependent features may behave unexpectedly. Both fields are stripped if the file is regenerated by some tools without awareness of Fabric requirements.

**Expected output (pass):**
```
✅ compatibilityMode: powerBI present in database.tmdl
✅ language field present in database.tmdl
```

**Expected output (fail):**
```
❌ Games.SemanticModel/definition/database.tmdl is missing 'compatibilityMode: powerBI'
```

**Fix:** restore or add the fields to `database.tmdl`:
```tmdl
database Games
    compatibilityLevel: 1700
    compatibilityMode: powerBI
    language: 1033
```

> Note: `language:` is an indented property inside the `database` block — the check uses `grep "language:"` (not anchored to line start).

---

### Report pre-flight validation checks

The **Validate Report structure** step runs after TMDL validation. It checks the `Games Report.Report/` folder.

#### Check 1 — `.platform` present and valid

**What it checks:** the file exists and `metadata.type` equals `"Report"`.

**Why:** Fabric uses `.platform` to register the folder as a Report item in the workspace.

#### Check 2 — `definition.pbir` present and valid

**What it checks:** the file exists and contains a `datasetReference` field.

**Why:** this file links the report to its semantic model. Without it Fabric cannot resolve the data source.

#### Check 3 — `report.json` present and has `sections`

**What it checks:** `Games Report.Report/report.json` exists and is valid JSON containing a `sections` array.

**Why:** this is the main report content file. Missing or malformed causes an immediate sync failure.

---

#### Adding new validation checks

To add a check, append a block to the `run:` section of the **Validate TMDL structure** step in `sync-to-fabric.yml`, following this pattern:

```bash
# N. Description of what you are checking
if <condition>; then
  echo "❌ Clear error message explaining what to fix"
  ERRORS=1
else
  echo "✅ What passed"
fi
```

Always increment `ERRORS` on failure rather than calling `exit 1` directly — this ensures all checks run and all failures are reported in a single job run.

---

## GitHub Secrets Required

| Secret | Description |
|---|---|
| `FABRIC_TENANT_ID` | Azure AD tenant ID |
| `FABRIC_CLIENT_ID` | Service Principal application (client) ID |
| `FABRIC_CLIENT_SECRET` | Service Principal client secret |
| `FABRIC_WORKSPACE_ID` | Fabric workspace GUID (`Copilot_Test`) |
| `FABRIC_GIT_CONNECTION_ID` | GUID of the GitHub ShareableCloud connection in Fabric |

---

## Service Principal Setup

The Service Principal (`fabric-cicd-sp`) must have:

- **Role:** Admin on the Fabric workspace (Contributor is not enough — `initializeConnection` requires Admin)
- **Git credentials:** A **ShareableCloud** GitHub connection (PAT-based) created in *Manage connections and gateways*, shared explicitly with the SP

Service Principals **cannot use Automatic credentials** with GitHub — always use `ConfiguredConnection` with a pre-created connection GUID.

---

## `updateFromGit` Notes

- Always returns **202 Accepted** (Long Running Operation). Never 200.
- The `Location` response header contains the polling URL.
- Poll `GET {Location}` every 15 s until `status = "Succeeded"` or `"Failed"`.
- When `workspaceHead` is `null` (workspace never synced via API), **omit** the field entirely from the request body. Sending `"workspaceHead": null` returns `InvalidParameter`.

---

## Adding New Tables or Columns

1. Make changes in Tabular Editor 3 and save as TMDL.
2. Verify no `description:` properties were added to columns — replace with `/// text` if needed.
3. Confirm `database.tmdl` and `definition.pbism` are still present and unchanged.
4. Push to `main` → workflow triggers automatically.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Required artifact is missing in 'definition.pbism'` | `definition.pbism` deleted or missing | Restore with `{"version":"4.0","settings":{}}` |
| `description is not a supported property in the current context` | `description:` on a column in TMDL | Replace with `/// text` doc comment |
| `Workload_FailedToParseFile` (generic) | Various TMDL parse issues | Check `database.tmdl` has `compatibilityMode: powerBI`; check annotation is at root in `model.tmdl` |
| `GitCredentialsNotConfigured` | SP has no Git credentials | Check `PATCH /git/myGitCredentials` step; verify connection GUID is correct |
| `ConnectionNotFound` | Wrong connection GUID, or connection not shared with SP | Get correct GUID from Fabric portal → *Manage connections*; share connection with SP |
| `InsufficientPrivileges` | SP is Contributor, not Admin | Upgrade SP to Admin on the workspace |
| `InvalidParameter: WorkspaceHead` | Sent `"workspaceHead": null` | Omit `workspaceHead` from request body when value is null |
| Workflow doesn't trigger on push | Changed only workflow file itself | Use *Actions → Run workflow* (manual dispatch) to test |
| Report loading bar never completes | Wrong report format (PBIR instead of old format) | Use `report.json` at report root (Fabric-native format), not `definition/pages/` PBIR structure. See **Games Report** section above. |
| `Git_InvalidResponseFromWorkload` on report sync | PBIR JSON schema violations | Switch to Fabric-native `report.json` format — PBIR is not supported for rendering even if sync succeeds. |
| Pre-flight: `No sections found in report.json` | `report.json` missing or not old format | Ensure `report.json` exists at `Games Report.Report/report.json` with a `sections` array |
| Report BPA: `Visual has no alt text` | Alt text not set on a visual | Set alt text in Power BI Desktop: Format pane → General → Alt text; or add it directly to the `singleVisual.objects.general[0].properties.altText` field in `report.json` |
| Report BPA: `Visual extends beyond page bounds` | Visual position + size exceeds page dimensions | Adjust `x`/`y`/`width`/`height` in the visual container so `x + width ≤ page width` and `y + height ≤ page height` |
| Report BPA: `Visuals overlap` | Two visuals share screen area | Reposition visuals so their bounding boxes do not intersect |
