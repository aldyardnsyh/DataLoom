# dataloom-contracts

Satu sumber kebenaran untuk bentuk data DataLoom: request dan response API,
kontrak DAG pipeline, serta output agent. Backend, worker, agent, dan frontend
memakai bentuk yang sama.

- Python package: `dataloom_contracts`
- Distribution: `dataloom-contracts`
- Pydantic v2, Python 3.11

## Impor

```python
from dataloom_contracts.common import ErrorResponse, Paginated
from dataloom_contracts.pipelines import PipelineDAG, PipelineNode, PipelineEdge, NodeType
from dataloom_contracts.agents import ProfileReport, CleaningProposal, PipelineDraft, SqlAnswer
from dataloom_contracts.runs import RunEvent, GateChecklist
```

Semua simbol juga tersedia dari root, tetapi submodul lebih eksplisit.

## Aturan kontrak

| Aturan | Alasan |
| --- | --- |
| `ContractModel` memakai `extra="forbid"` | Output agent dengan key tak dikenal harus gagal validasi, bukan dibuang diam-diam |
| `ReadModel` memakai `extra="ignore"` | Menambah kolom DB tidak boleh merusak klien lama |
| `CleaningOp` adalah katalog tertutup | Agent tidak bisa menyelundupkan operasi yang tidak bisa dieksekusi worker |
| Setiap output agent punya `status` | `insufficient_evidence` adalah hasil sah, bukan error |
| `PipelineDAG` divalidasi deterministik | Siklus, edge yatim, node tak terhubung, dan titik masuk ganda ditolak |
| ID memakai `uuid.UUID`, waktu timezone-aware | Konsisten dengan `TIMESTAMPTZ` Postgres |

## Peta modul

| File | Isi |
| --- | --- |
| `_base.py` | `ContractModel`, `ReadModel`, seluruh enum |
| `common.py` | Error response, validation issue, pagination, OK response |
| `auth.py` | Register, login, user, organisasi, membership, session |
| `projects.py` | Proyek dan pengaturan proyek |
| `sources.py` | Sumber, API key, rotasi |
| `uploads.py` | Init, part, complete, output unggahan |
| `pipelines.py` | Node, edge, DAG, versi, validator |
| `runs.py` | Run, node, event SSE, gate checklist |
| `agents.py` | Profil, usulan, draft pipeline, kualitas, diagnosis, SQL |
| `datasets.py` | Dataset, preview, lineage |
| `usage.py` | Usage, LLM call, budget, audit, queue |
| `ingest.py` | Accepted, rejected, idempotency |

## Tipe TypeScript

Tipe frontend dihasilkan dari OpenAPI, bukan ditulis ulang dengan tangan.

```bash
python packages/contracts/openapi_export.py --out apps/web/openapi.json
npx openapi-typescript apps/web/openapi.json -o apps/web/lib/api-types.ts
cd apps/web && npx tsc --noEmit
```

Backend wajib mengisi `response_model` pada setiap endpoint agar skema OpenAPI
lengkap.