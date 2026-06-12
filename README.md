# Lustre Project Quota API

A REST API for managing Lustre project quotas and **LQA (Lustre Quota Aggregation)**. It implements a two-tier directory quota architecture where Level-1 (L1) directories are governed by an LQA aggregate quota, and Level-2 (L2) subdirectories carry individual project quotas that roll up into the parent LQA.

---

## Architecture Overview

```
Lustre filesystem (e.g. "aifs")
│
├── /lustre/aifs/proj_A          ← L1 directory (tier 1) — container, projid = 0
│   │   governed by LQA "proj_A" (ranges: 20001-30000)
│   │   (L1 itself has no project ID; only L2 subdirs carry projids)
│   │
│   ├── /lustre/aifs/proj_A/user1   ← L2 directory (tier 2)  projid = 20001
│   ├── /lustre/aifs/proj_A/user2   ← L2 directory (tier 2)  projid = 20002
│   └── /lustre/aifs/proj_A/tmp     ← not yet assigned (projid = 0)
│
└── /lustre/aifs/standalone      ← standalone directory (tier 0)
    projid = 99001               no LQA association
```

**Key conventions:**

| Concept | Rule |
|---|---|
| LQA name | Must equal the **basename** of the L1 directory |
| Tier 1 (L1) | Container directory; `basename(dir) == LQA name`; **projid stays 0** (no Lustre project ID assigned) |
| Tier 2 (L2) | Immediate child of an L1 directory; carries its own `projid ∈ LQA ranges` |
| Tier 0 | Standalone directory with a project ID but no LQA association |
| LQA aggregate quota | Block-only; governs the **sum** of all L2 project IDs' disk usage under this L1. No inode limits at LQA level. |
| LQA operations | Must run on the MGS node (`lctl lqa_*`). Use `LUSTRE_API_LQA_MODE=ssh` when the API is deployed on a client |
| Block quota display | All block values are returned as **human-readable strings** (e.g. `"4k"`, `"3.023G"`, `"100G"`, `"1638P"`) matching `lfs quota -h` output |
| Block quota input | Accepts **bytes** (integer) or **unit strings** such as `"100G"`, `"1T"`, `"500M"`. `0` means unlimited. |
| Inode quota | Supported for individual project quotas only; not available at LQA level |
| Accurate usage | `lfs quota -P --lqa` returns QMT grant-based usage (`block_granted`). Use `?accurate_usage=true` for the precise sum from `lfs quota -ap`, filtered by LQA ranges. |
| Consistency check | Use `?check_consistency=true` to scan L2 subdirs for missing or out-of-range project IDs. Returns `misconfigured_dirs` list. Separate from `accurate_usage` to avoid unnecessary filesystem scans. |
| Directory paths | All directory API endpoints accept **relative** paths (e.g. `"proj_A/user1"`, relative to the mountpoint) or absolute paths. Responses always return the resolved absolute path. |

**Typical workflow for setting up a project group:**

```
1. POST /api/v1/filesystems/aifs/lqas                            → create LQA "proj_A"
2. POST /api/v1/filesystems/aifs/lqas/proj_A/ranges              → add range 20001-30000
3. PUT  /api/v1/filesystems/aifs/lqas/proj_A/quota               → set aggregate quota (e.g. "10T")
4. GET  /api/v1/filesystems/aifs/directories/next-projid?lqa=proj_A  → predict next free projid (e.g. 20001)
5. PUT  /api/v1/filesystems/aifs/directories/project             → assign proj_A/user1 projid=20001
6. PUT  /api/v1/filesystems/aifs/directories/project             → assign proj_A/user2 projid=20002
   (steps 5–6 call lfs project -srp; paths may be relative, e.g. "proj_A/user1")
```

---

## Requirements

- Python ≥ 3.11
- Lustre client with `lfs` (≥ 2.14) installed on the API host
- For LQA operations in **local** mode: the API host must be the MGS/MDS node
- For LQA operations in **SSH** mode: SSH key-based access to the MGS node(s)

---

## Quick Start

```bash
# Clone / enter project directory
cd /home/tozhang/program/lustre-prjquota-api

# Install dependencies (use a virtual environment in production)
pip install -r requirements.txt

# Start the server (defaults: 0.0.0.0:8000)
uvicorn main:app --host 0.0.0.0 --port 8000

# Interactive API docs
open http://localhost:8000/docs
```

---

## Configuration

All settings are read from environment variables with the prefix `LUSTRE_API_`, or from a `.env` file in the working directory.

| Variable | Default | Description |
|---|---|---|
| `LUSTRE_API_LFS_PATH` | `/usr/bin/lfs` | Path to the `lfs` binary |
| `LUSTRE_API_LCTL_PATH` | `/usr/sbin/lctl` | Path to the `lctl` binary |
| `LUSTRE_API_LQA_MODE` | `local` | `local` — lctl/lfs runs on this host; `ssh` — runs on a remote MGS via SSH |
| `LUSTRE_API_MGS_HOSTS` | *(empty)* | Comma-separated list of MGS host addresses (used in SSH mode). Example: `192.168.1.10,192.168.1.11` |
| `LUSTRE_API_SSH_USER` | `root` | SSH user for MGS access |
| `LUSTRE_API_SSH_KEY` | *(none)* | **Required** when `lqa_mode=ssh`. Absolute path to SSH private key file. Password-based auth is not supported. |
| `LUSTRE_API_SSH_PORT` | `22` | SSH port |
| `LUSTRE_API_MOUNTPOINTS` | *(none)* | JSON dict mapping fsname → mountpoint. **Required** in SSH mode (no `/proc/mounts` on remote). Example: `{"aifs": "/lustre/aifs/client"}` |
| `LUSTRE_API_MGS_CACHE_TTL` | `60` | Seconds to cache the active MGS host before re-probing |
| `LUSTRE_API_LFS_TIMEOUT` | `30` | Timeout (seconds) for `lfs` subprocess calls |
| `LUSTRE_API_LCTL_TIMEOUT` | `30` | Timeout (seconds) for `lctl` subprocess calls |
| `LUSTRE_API_SSH_CONNECT_TIMEOUT` | `5` | Timeout (seconds) for SSH handshake |
| `LUSTRE_API_ASSIGNMENT_STORE_PATH` | `./data/assignments.json` | Path to the JSON assignment store file |

### Example `.env` file (SSH mode, HA MGS pair)

```dotenv
LUSTRE_API_LQA_MODE=ssh
LUSTRE_API_MGS_HOSTS=192.168.1.10,192.168.1.11
LUSTRE_API_SSH_USER=root
LUSTRE_API_SSH_KEY=/root/.ssh/lustre_mgs_key
LUSTRE_API_MOUNTPOINTS={"aifs": "/lustre/aifs/client"}
LUSTRE_API_MGS_CACHE_TTL=120
LUSTRE_API_ASSIGNMENT_STORE_PATH=/var/lib/lustre-api/assignments.json
```

---

## API Reference

Base URL: `http://<host>:8000/api/v1`

Interactive docs: `http://<host>:8000/docs`

### Health

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Returns `{"status": "ok"}` |
| GET | `/api/v1/health/mgs` | Diagnostic: runs `lctl dl` and `lqa_list` on the active MGS; returns raw output for troubleshooting |

### Project Quota — `/api/v1/filesystems/{fsname}/quotas/projects`

Block limit fields in requests accept **bytes (int)** or **unit strings** (`"100G"`, `"1T"`, `"500M"`). `0` means unlimited.
Block fields in responses are **human-readable strings** (e.g. `"4k"`, `"10G"`). Inode fields remain integers.

| Method | Path | Description |
|---|---|---|
| GET | `/{projid}` | Get quota for a project ID |
| PUT | `/{projid}` | Set quota limits (`block_softlimit`, `block_hardlimit`, `inode_softlimit`, `inode_hardlimit`) |
| DELETE | `/{projid}` | Delete quota record for a project ID |
| POST | `/{projid}/reset` | Reset usage counters for a project ID |
| GET | `/default` | Get default project quota (add `?pool=...` for pool-specific) |
| PUT | `/default` | Set default project quota |
| GET | `/grace` | Get grace times |
| PUT | `/grace` | Set grace times |

**Example response** (`GET /{projid}`):
```json
{
  "projid": 20001,
  "block_softlimit": "0k",
  "block_hardlimit": "2G",
  "block_usage": "512M",
  "inode_softlimit": 0,
  "inode_hardlimit": 1000000,
  "inode_usage": 42,
  "block_grace": "-",
  "inode_grace": "-"
}
```

### LQA — `/api/v1/filesystems/{fsname}/lqas`

LQA quota is **block-only** (no inode limits at the aggregate level).

| Method | Path | Description |
|---|---|---|
| GET | `/` | List all LQAs with their ranges |
| POST | `/` | Create a new LQA |
| GET | `/{name}` | Get LQA details (name, ranges, quota). Supports `?accurate_usage=true` and `?check_consistency=true` |
| DELETE | `/{name}` | Destroy an LQA |
| GET | `/{name}/ranges` | List project ID ranges for an LQA |
| POST | `/{name}/ranges` | Add a range (overlap with other LQAs is rejected) |
| DELETE | `/{name}/ranges/{start}-{end}` | Remove a range |
| GET | `/{name}/quota` | Get LQA quota. Supports `?accurate_usage=true` and `?check_consistency=true` |
| PUT | `/{name}/quota` | Set LQA quota limits (`block_softlimit`, `block_hardlimit` only) |
| GET | `/{name}/grace` | Get LQA grace times |
| PUT | `/{name}/grace` | Set LQA grace times |

#### LQA Quota Query Parameters

| Parameter | Default | Description |
|---|---|---|
| `accurate_usage` | `false` | Compute `actual_block_usage` by summing all projids within the LQA ranges via `lfs quota -ap`. Fast — one batch call regardless of directory count. |
| `check_consistency` | `false` | Scan L2 subdirectories of every L1 dir belonging to this LQA. Detects subdirs with `projid=0` or projid outside LQA ranges. Returns `misconfigured_dirs` list and `usage_warning`. Slower — O(L1 count) `find` + `lfs project -d` batch calls. |

**Example response** (`GET /{name}?accurate_usage=true&check_consistency=true`):
```json
{
  "name": "LQA_prj1",
  "fsname": "aifs",
  "ranges": [{"start": 10001, "end": 20000}],
  "quota": {
    "lqa_name": "LQA_prj1",
    "fsname": "aifs",
    "block_softlimit": "0k",
    "block_hardlimit": "10G",
    "block_granted": "3.023G",
    "actual_block_usage": "2.9G",
    "usage_warning": null,
    "misconfigured_dirs": null
  }
}
```

**Example response when inconsistency is detected:**
```json
{
  "block_granted": "3.023G",
  "actual_block_usage": "2.9G",
  "usage_warning": "usage結果可能不準確，因爲：子目錄未設置lqa範圍内的project id",
  "misconfigured_dirs": [
    "/lustre/aifs/LQA_prj1/user3"
  ]
}
```

### Directory Project — `/api/v1/filesystems/{fsname}/directories`

`path` parameters accept a **relative** path (e.g. `"proj_A/user1"`, relative to the mountpoint) or an absolute path.

| Method | Path | Description |
|---|---|---|
| GET | `/project?path=...` | Get project ID for a directory |
| PUT | `/project` | Set project ID for a directory (`lfs project -srp`) |
| DELETE | `/project?path=...` | Clear project ID from a directory (`lfs project -C`) |
| GET | `/unassigned?lqa=...` | Scan `<mountpoint>/<lqa>` for subdirs with `projid=0`; returns `next_projid`. LQA ranges fetched in real time — no store lookup. |
| GET | `/next-projid?lqa=...` | Predict the next available project ID within the LQA ranges. Scans L2 subdirs in real time and returns the first unused projid. |

---

## Error Responses

All errors return JSON:

```json
{
  "code": "LQA_NOT_FOUND",
  "message": "LQA 'proj_X' not found in filesystem 'aifs'",
  "detail": {"fsname": "aifs", "name": "proj_X"}
}
```

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `INVALID_PARAMETER` | Bad request parameter |
| 400 | `PATH_NOT_UNDER_MOUNTPOINT` | Path is outside the Lustre mountpoint |
| 403 | `PERMISSION_DENIED` | Insufficient privilege for `lfs`/`lctl` command |
| 404 | `FILESYSTEM_NOT_FOUND` | Filesystem not mounted on this node |
| 404 | `QUOTA_NOT_FOUND` | Project quota record does not exist |
| 404 | `LQA_NOT_FOUND` | LQA does not exist |
| 404 | `DIRECTORY_NOT_FOUND` | Directory path does not exist |
| 409 | `LQA_ALREADY_EXISTS` | LQA name is already taken |
| 409 | `RANGE_CONFLICT` | Range overlaps with an existing LQA's ranges |
| 422 | `PROJID_OUT_OF_LQA_RANGE` | Project ID is outside the governing LQA ranges |
| 503 | `LUSTRE_TIMEOUT` | `lfs`/`lctl` command timed out |
| 503 | `LUSTRE_ERROR` | `lfs`/`lctl` command returned a non-zero exit code |
| 503 | `MGS_NOT_FOUND` | No active MGS found among configured `mgs_hosts` |

---

## Project Structure

```
lustre-prjquota-api/
├── main.py                    # FastAPI app factory, startup event
├── config.py                  # pydantic-settings configuration
├── errors.py                  # Exception hierarchy + error handler
├── requirements.txt
├── data/
│   └── assignments.json       # Created automatically on first start
├── schemas/
│   ├── common.py
│   ├── project_quota.py       # Union[int,str] block limits; str block response fields
│   ├── lqa.py                 # Block-only LQA quota; accurate_usage / check_consistency
│   ├── directory.py           # Relative/absolute path models; NextProjidResponse
│   └── assignment.py
├── store/
│   └── assignment_store.py    # Thread-safe JSON store (used internally by LQA service)
├── adapters/
│   ├── lfs.py                 # lfs wrappers; -h quota parsing; batch project query
│   └── lctl.py                # lctl wrappers + MGS HA cache/failover; SSH routing
├── services/
│   ├── filesystem.py          # /proc/mounts parser + LUSTRE_API_MOUNTPOINTS support
│   ├── project_quota.py
│   ├── lqa.py                 # accurate usage (range-based) + consistency scan (batch)
│   └── directory.py           # path resolution; real-time L2 scan; next-projid prediction
└── api/v1/
    ├── router.py
    ├── project_quota.py
    ├── lqa.py
    └── directory.py
```
