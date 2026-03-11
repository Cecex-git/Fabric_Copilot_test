# Fabric CI/CD Guide — GitHub → Microsoft Fabric Sync

This guide documents the setup, rules, and lessons learned for automatically syncing this Power BI semantic model from GitHub to the Microsoft Fabric `Copilot_Test` workspace.

---

## Architecture Overview

```
GitHub (main branch)
  └─ push to Games.SemanticModel/**
       └─ GitHub Actions: sync-to-fabric.yml
            ├─ Pre-flight TMDL validation
            ├─ Entra ID token (Service Principal)
            ├─ Configure SP Git credentials (Fabric API)
            ├─ Check Git status (Fabric API)
            └─ updateFromGit → LRO polling → ✅ Fabric updated
```

---

## Repository Structure

```
Games.SemanticModel/
  definition.pbism                  ← Required Fabric item descriptor
  .platform                         ← Fabric Git metadata (logicalId, type)
  definition/
    database.tmdl                   ← Compatibility + language settings
    model.tmdl                      ← Model-level metadata and annotations
    tables/
      Games.tmdl                    ← Table columns, measures, partition (M query)

.github/workflows/
  sync-to-fabric.yml                ← CI/CD sync workflow

docs/
  fabric-cicd-guide.md              ← This file
```

---

## Required Files — Do Not Delete

| File | Why it is required |
|---|---|
| `definition.pbism` | Fabric Git integration entry point. Without it every sync fails with `Required artifact is missing in 'definition.pbism'`. Content must be valid JSON with a `version` field. |
| `definition/database.tmdl` | Must contain `compatibilityMode: powerBI` and `language: 1033`. Missing either causes a silent parse failure in Fabric. |
| `definition/model.tmdl` | Must include `ref table Games` and any root-level annotations. |
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

## GitHub Actions Workflow

**File:** `.github/workflows/sync-to-fabric.yml`

**Triggers:**
- Push to `main` with changes under `Games.SemanticModel/**`
- Manual dispatch (`workflow_dispatch`)

### Step summary

| Step | What it does |
|---|---|
| Checkout | Checks out repo files (required for validation) |
| Validate TMDL structure | Pre-flight checks — see below. Fails fast before any API call. |
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
