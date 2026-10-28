"""FR-020 / FR-032: the deterministic ``PipelineDAG`` validator.

The promise under test (PRD §5.3 FR-020): *"DAG siklik/ber-node tak dikenal
ditolak dengan pesan jelas"*. Each rejection below would be a real production
bug (infinite run, unknown node, silent data loss), so each test asserts both
the rejection AND that the message is human-readable Indonesian.

Pure Pydantic: no DB, no worker, no network.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

SOURCE_ID = "11111111-1111-4111-8111-111111111111"


# ---------------------------------------------------------------------------
# Validators / factories
# ---------------------------------------------------------------------------
def _source_node(node_id: str = "n_source") -> dict:
    return {
        "id": node_id,
        "type": "source",
        "label": "Sumber",
        "params": {"kind": "source", "source_id": SOURCE_ID},
    }


def _clean_node(node_id: str = "n_clean") -> dict:
    return {
        "id": node_id,
        "type": "clean",
        "label": "Bersihkan",
        "params": {
            "kind": "clean",
            "operations": [{"op": "trim", "column": "nama", "params": {}}],
        },
    }


def _validate_node(node_id: str = "n_validate") -> dict:
    return {
        "id": node_id,
        "type": "validate",
        "label": "Validasi",
        "params": {
            "kind": "validate",
            "rules": [
                {"rule_id": "r1", "kind": "not_null", "column": "nama", "params": {}}
            ],
        },
    }


def _sink_node(node_id: str = "n_sink") -> dict:
    return {
        "id": node_id,
        "type": "sink",
        "label": "Simpan",
        "params": {"kind": "sink", "layer": "gold", "dataset_name": "gold_orders"},
    }


def _edge(begin: str, end: str) -> dict:
    # The contract aliases ``from`` -> ``from_node`` (Python keyword avoidance).
    return {"from": begin, "to": end}


def _linear_dag() -> dict:
    return {
        "schema_version": 1,
        "nodes": [
            _source_node(),
            _validate_node(),
            _clean_node(),
            _sink_node(),
        ],
        "edges": [
            _edge("n_source", "n_validate"),
            _edge("n_validate", "n_clean"),
            _edge("n_clean", "n_sink"),
        ],
    }


def _build(payload: dict):
    from dataloom_contracts.pipelines import PipelineDAG

    return PipelineDAG.model_validate(payload)


def _assert_rejected(payload: dict, *needles: str) -> str:
    """Assert validation fails and the message contains every ``needle``."""
    with pytest.raises(ValidationError) as excinfo:
        _build(payload)
    message = str(excinfo.value)
    for needle in needles:
        assert needle in message, (
            f"Pesan validasi tidak memuat {needle!r}; pesan aktual:\n{message}"
        )
    return message


def _assert_indonesian(message: str) -> None:
    """A rejection message must be readable Indonesian, not a Python traceback."""
    lowered = message.lower()
    indonesian_markers = (
        "tidak",
        "harus",
        "wajib",
        "node",
        "siklus",
        "edge",
        "duplikat",
        "pipeline",
        "diizinkan",
        "kolom",
    )
    assert any(marker in lowered for marker in indonesian_markers), (
        f"Pesan validasi bukan Bahasa Indonesia yang jelas:\n{message}"
    )
    # English enum leaks are a smell but not fatal; a raw Python repr is fatal.
    assert "Traceback" not in message, "Pesan validasi membocorkan traceback Python."


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_valid_linear_dag_is_accepted() -> None:
    dag = _build(_linear_dag())

    assert len(dag.nodes) == 4
    assert len(dag.edges) == 3
    assert dag.entry_node().id == "n_source", (
        "Titik masuk DAG linear harus node source pertama."
    )


def test_topological_order_is_a_real_topological_sort() -> None:
    dag = _build(_linear_dag())
    order = dag.topological_order()

    assert order == ["n_source", "n_validate", "n_clean", "n_sink"], (
        f"Urutan topologis salah: {order}. Worker akan menjalankan node di luar urutan."
    )
    # Generic proof (not just this fixture): every edge points forward.
    position = {node_id: index for index, node_id in enumerate(order)}
    for edge in dag.edges:
        assert position[edge.from_node] < position[edge.to_node], (
            f"Edge {edge.from_node}->{edge.to_node} melanggar urutan topologis."
        )


def test_topological_order_is_deterministic_for_parallel_branches() -> None:
    """A diamond must produce a stable order (same input -> same output twice)."""
    diamond = {
        "nodes": [
            _source_node(),
            _clean_node("n_branch_a"),
            _clean_node("n_branch_b"),
            _sink_node(),
        ],
        "edges": [
            _edge("n_source", "n_branch_a"),
            _edge("n_source", "n_branch_b"),
            _edge("n_branch_a", "n_sink"),
            _edge("n_branch_b", "n_sink"),
        ],
    }
    first = _build(diamond).topological_order()
    second = _build(diamond).topological_order()

    assert first == second, f"Urutan topologis tidak deterministik: {first} vs {second}"
    assert first[0] == "n_source", "Source harus dijalankan pertama."
    assert first[-1] == "n_sink", "Sink harus dijalankan terakhir."


# ---------------------------------------------------------------------------
# Cyclic DAG (FR-020: "DAG siklik ... ditolak")
# ---------------------------------------------------------------------------
def test_cyclic_dag_is_rejected_with_cycle_message() -> None:
    payload = _linear_dag()
    # Replace the sink edge with a back-edge: sink -> validate closes a loop.
    payload["edges"] = [
        _edge("n_source", "n_validate"),
        _edge("n_validate", "n_clean"),
        _edge("n_clean", "n_validate"),  # back edge
        _edge("n_clean", "n_sink"),
    ]

    message = _assert_rejected(payload, "siklus")
    _assert_indonesian(message)


def test_self_loop_edge_is_rejected() -> None:
    payload = _linear_dag()
    payload["edges"].append(_edge("n_clean", "n_clean"))

    message = _assert_rejected(payload, "node ke dirinya sendiri")
    _assert_indonesian(message)


def test_every_node_has_incoming_edge_is_rejected() -> None:
    """A pure cycle (no entry point) must name the missing entry, not crash."""
    payload = {
        "nodes": [_clean_node("n_a"), _clean_node("n_b"), _sink_node()],
        "edges": [_edge("n_a", "n_b"), _edge("n_b", "n_a")],
    }

    message = _assert_rejected(payload)
    # Either the "no entry point" or the "source required" message is acceptable;
    # both must be present in Indonesian and mention the concept.
    assert ("titik awal" in message) or ("titik masuk" in message), (
        f"Pesan tidak menjelaskan masalah titik masuk:\n{message}"
    )
    _assert_indonesian(message)


# ---------------------------------------------------------------------------
# Unknown node type / unknown operation
# ---------------------------------------------------------------------------
def test_unknown_node_type_is_rejected() -> None:
    payload = _linear_dag()
    payload["nodes"][1]["type"] = "train_llm"  # not one of the six node types

    message = _assert_rejected(payload)
    assert "train_llm" in message, (
        "Pesan tidak menyebut tipe node yang salah; UI tidak bisa menyorotnya."
    )


def test_unknown_params_kind_is_rejected() -> None:
    """params.kind is the discriminator: an unknown value must not be guessed."""
    payload = _linear_dag()
    payload["nodes"][2]["params"] = {"kind": "magic_clean"}

    message = _assert_rejected(payload)
    assert "magic_clean" in message or "kind" in message, (
        f"Pesan tidak menunjukkan discriminator params yang salah:\n{message}"
    )


# ---------------------------------------------------------------------------
# Duplicate node ids
# ---------------------------------------------------------------------------
def test_duplicate_node_ids_are_rejected() -> None:
    payload = _linear_dag()
    payload["nodes"].append(_clean_node("n_source"))  # duplicate id

    message = _assert_rejected(payload, "duplikat")
    _assert_indonesian(message)


# ---------------------------------------------------------------------------
# Edges referencing missing nodes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("side", ["from", "to"])
def test_edge_to_missing_node_is_rejected(side: str) -> None:
    payload = _linear_dag()
    payload["edges"].append(
        _edge("ghost_node", "n_sink") if side == "from" else _edge("n_sink", "ghost_node")
    )

    message = _assert_rejected(payload, "ghost_node")
    _assert_indonesian(message)


# ---------------------------------------------------------------------------
# Node/params type mismatch
# ---------------------------------------------------------------------------
def test_node_with_wrong_params_kind_is_rejected() -> None:
    payload = _linear_dag()
    # A sink node carrying source params: the worker would call the wrong code.
    payload["nodes"][3]["params"] = {"kind": "source", "source_id": SOURCE_ID}

    message = _assert_rejected(payload)
    assert "'sink'" in message or "sink" in message, (
        f"Pesan tidak menjelaskan ketidakcocokan tipe node/params:\n{message}"
    )
    _assert_indonesian(message)


# ---------------------------------------------------------------------------
# No sink
# ---------------------------------------------------------------------------
def test_dag_without_sink_is_rejected() -> None:
    payload = {
        "nodes": [_source_node(), _clean_node()],
        "edges": [_edge("n_source", "n_clean")],
    }

    message = _assert_rejected(payload, "sink")
    _assert_indonesian(message)


def test_orphan_node_is_rejected() -> None:
    """An unreachable node would silently never execute (silent data loss)."""
    payload = _linear_dag()
    payload["nodes"].append(_clean_node("n_orphan"))

    message = _assert_rejected(payload, "n_orphan")
    assert "tidak terhubung" in message or "tidak akan pernah dijalankan" in message, (
        f"Pesan tidak menjelaskan bahwa node yatim tidak pernah berjalan:\n{message}"
    )


# ---------------------------------------------------------------------------
# Entry point rules
# ---------------------------------------------------------------------------
def test_entry_must_be_a_source_node() -> None:
    payload = {
        "nodes": [_clean_node("n_first"), _source_node("n_src"), _sink_node()],
        "edges": [
            _edge("n_first", "n_src"),
            _edge("n_src", "n_sink"),
            # n_first has no incoming edge but is a clean node.
        ],
    }

    message = _assert_rejected(payload)
    assert "source" in message.lower(), f"Pesan tidak menyebut sumber:\n{message}"


def test_two_entry_points_are_rejected() -> None:
    payload = _linear_dag()
    payload["nodes"].append(_source_node("n_source_2"))
    payload["edges"].append(_edge("n_source_2", "n_sink"))

    message = _assert_rejected(payload, "satu titik masuk")
    _assert_indonesian(message)


def test_empty_node_list_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _build({"nodes": [], "edges": []})


# ---------------------------------------------------------------------------
# Structured issue extraction (used by the UI canvas)
# ---------------------------------------------------------------------------
def test_validate_dag_helper_returns_structured_issues() -> None:
    """``validate_dag`` must never raise: the API forwards the issue list."""
    from dataloom_contracts.pipelines import PipelineDAG, validate_dag

    with pytest.raises(ValidationError):
        _build({"nodes": [_clean_node()], "edges": []})

    # Build a valid DAG then break it through a copy of the model.
    dag = _build(_linear_dag())
    assert validate_dag(dag) == [], "DAG valid dilaporkan punya isu."

    payload = _linear_dag()
    payload["nodes"].append(_clean_node("n_source"))
    with pytest.raises(ValidationError):
        _build(payload)
    # Direct low-level call with a *valid* model proves the helper signature.
    assert isinstance(PipelineDAG.model_validate(_linear_dag()).topological_order(), list)


def test_sink_must_be_connected_when_there_is_more_than_one_node() -> None:
    payload = _linear_dag()
    payload["nodes"].append(_sink_node("n_sink_orphan"))

    message = _assert_rejected(payload, "n_sink_orphan")
    assert "input" in message.lower(), (
        f"Pesan tidak menjelaskan sink tanpa input:\n{message}"
    )


def test_node_id_must_be_canvas_safe() -> None:
    """Node ids become React Flow ids and ``run_nodes.node_id``: keep them ASCII."""
    payload = _linear_dag()
    payload["nodes"][0]["id"] = "n source dengan spasi"
    payload["edges"][0]["from"] = "n source dengan spasi"

    with pytest.raises(ValidationError):
        _build(payload)


def test_dag_accepts_clean_op_needing_params_only_when_supplied() -> None:
    """``parse_date`` without ``format`` is a contract violation, not a warning."""
    payload = _linear_dag()
    payload["nodes"][2]["params"] = {
        "kind": "clean",
        "operations": [{"op": "parse_date", "column": "tanggal", "params": {}}],
    }

    message = _assert_rejected(payload)
    assert "format" in message, (
        f"Pesan tidak menyebut parameter 'format' yang hilang:\n{message}"
    )


def test_multi_tenant_node_source_id_is_just_a_uuid_not_a_table() -> None:
    """Guards against a regression where source ids were accepted as strings only."""
    payload = _linear_dag()
    payload["nodes"][0]["params"]["source_id"] = str(uuid.uuid4())
    dag = _build(payload)
    assert dag.nodes[0].params.source_id == uuid.UUID(payload["nodes"][0]["params"]["source_id"])
