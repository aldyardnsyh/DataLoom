# Security Review — Self-Healing Wiring

Scope: `apps/agents/app/graphs/self_healing.py`, `apps/agents/app/runner.py`
(pendukung: `app/prompts/healing.py`, `app/tools/logs.py`, `app/llm/budget.py`).
Tanggal: 2026-10-30. Metode: white-box static review, tanpa eksekusi.

## Ringkasan: 4 PASS / 3 FAIL — 1 High, 1 Medium, 1 Low

| # | Kontrol | Verdict | Severity bila FAIL |
|---|---------|---------|--------------------|
| (a) | Log/error sebagai DATA (`wrap_untrusted_data`) | PASS | — |
| (b) | Fix tidak menimpa versi lama (new version only) | PASS | — |
| (c) | Agent tidak re-run pipeline sendiri | PASS | — |
| (d) | Approval manusia sebelum apply | PASS | — |
| (e) | Batas 2 proposal per failed run | FAIL | Medium |
| (f) | Budget guard | PASS | — |
| (g) | Tenant isolation (`org_id` di semua query) | FAIL (2 lokasi) | High |
| (+1) | `previous_error` tidak di-wrap | FAIL | Low |

---

### (a) PASS — log/error diperlakukan sebagai DATA
- `self_healing.py:76-80`: `error_message` di-wrap `wrap_untrusted_data(..., "run_error_message")`.
- `prompts/healing.py:80`: tiap `excerpt` log di-wrap ulang (`"log_excerpt"`) — defense-in-depth, independent dari `collect_evidence`.
- `tools/logs.py:137,164,178,231`: `redact()` + batas panjang sebelum masuk prompt.

### (b) PASS — new version only, tidak menimpa versi lama
- `runner.py:491-540` `apply_healing_fix`: docstring eksplisit "no pipeline DB write… hanya memvalidasi + mengembalikan rencana aksi; apps/api yang mengeksekusi new_version + requeue". Return `{"new_version_needed": True, ...}`. Tidak ada `UPDATE/INSERT` ke tabel pipeline/version di wiring ini.
- `runner.py:165-176`: teks interrupt menegaskan "membuat versi pipeline BARU (tidak mengubah versi lama)".

### (c) PASS — agent tidak re-run pipeline sendiri
- Graph `runner.py:185-205` berakhir di `approval`/`needs_human` → `END`. Tidak ada edge ke eksekusi pipeline / requeue. Requeue dilakukan apps/api di luar fungsi ini.

### (d) PASS — approval manusia sebelum apply
- `runner.py:166` `interrupt({kind: "healing_fix_approval", ...})` menghentikan graph sampai `resume_agent` dipanggil dengan keputusan user.
- `runner.py:491-520` `apply_healing_fix` menolak bila tidak ada proposal `APPROVED` (`ContractViolation` bila `proposal is None`).

### (e) FAIL (Medium) — batas 2 proposal tidak ditegakkan
- `self_healing.py:39` mendefinisikan `MAX_PROPOSALS_PER_RUN = 2`, dan docstring `persist_healing_proposal` (`self_healing.py:196-197`) mengklaim "Cap: at most 2 rows per failed run".
- Namun `persist_healing_proposal` (`self_healing.py:190-238`) **tidak pernah menghitung proposal existing** (tidak ada `SELECT count(...) WHERE agent_run_id=...`) dan tidak menolak/membatasi insert. Konstanta hanya di-assert test (`tests/unit/test_self_healing_wiring.py:37`), bukan di-enforce runtime.
- PoC: panggil `start_healing` → approve → `resume_agent` → ulangi `persist_healing_proposal` 3x untuk `agent_run_id` sama → 3 baris `AgentProposal` tersimpan, tidak ada error.
- Rekomendasi: `SELECT func.count() WHERE agent_run_id=:id AND kind='fix_proposal'` sebelum insert; raise `ContractViolation("batas 2 proposal tercapai")` bila >= 2.

### (f) PASS — budget guard
- `runner.py:395,466` `BudgetGuard(session, org_id, agent_run_id)` dibangun di `start_healing` dan `resume_agent`; `diagnose` (`self_healing.py:109-120`) memanggil `call_llm(..., guard=guard, ...)`, dan `call_llm` (`graphs/base.py:101`) me-raise `BudgetExceeded` sebelum call. `BudgetExceeded` ditangani di `runner.py:418-424,482-486`.

### (g) FAIL (High) — 2 query tanpa filter `org_id` (tenant isolation bocor)
1. `runner.py:455-457` `resume_agent`: `select(AgentRun).where(AgentRun.thread_id == thread_id)` — **tanpa `org_id`**. `thread_id` format `{type}:{uuid4}` tidak rahasia-kuat bila bocor via log; penyerang antar-tenant yang menebak/mendapat `thread_id` dapat me-resume graph tenant lain dan menyetujui fix-nya.
   PoC: Tenant B memanggil `resume_agent(thread_id="<thread milik tenant A>", approved_proposal_ids=[...])` → `scalar_one_or_none()` mengembalikan run milik A → graph A dilanjutkan atas otoritas B.
   Rekomendasi: wajib parameter `org_id` di `resume_agent` dan `WHERE thread_id=:t AND org_id=:o`.
2. `runner.py:530-532` `apply_healing_fix`: `select(AgentRun).where(AgentRun.id == agent_run_id)` — **tanpa `org_id`**, lalu `input_json` run dibaca untuk `pipeline_id` (`runner.py:533-534`). Proposal query-nya sendiri sudah org-scoped (`runner.py:509-513`), tetapi `pipeline_id` diambil dari run lintas-tenant bila `agent_run_id` diketahui. Dampak terbatas (hanya read `pipeline_id`), severity ikut High karena satu pola.
   Rekomendasi: tambah `AgentRun.org_id == org_id` di filter run.

- Org-scoped dengan benar (tidak bermasalah): `tools/logs.py:148,170` (`Run`/`RunNode` + `org_id`), `apply_healing_fix` proposal query (`runner.py:509-513`), `get_report` (`runner.py:549-551`).

### (+1) FAIL (Low) — `previous_error` tidak di-wrap
- `prompts/healing.py:94-95`: `previous_error[:1000]` disisipkan mentah ke prompt ("## Perbaikan yang diperlukan") tanpa `wrap_untrusted_data`, tidak seperti `error_message` dan `excerpt`. `previous_error` berasal dari `state["last_error"]` yang dapat berisi teks error upstream. Dampak: prompt-injection via pesan error; severity Low karena `last_error` biasanya ditulis sistem sendiri, bukan tenant lain.
- Rekomendasi: `wrap_untrusted_data(previous_error[:1000], "previous_error")`.
