from hashlib import sha256
from pathlib import Path

import yaml

from project_agent_controller.domain.models import ProjectConfig
from project_agent_controller.knowledge.index import KnowledgeIndex
from project_agent_controller.storage.database import Database


def write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_prompt_requires_version_contract_and_matching_body_hash(settings) -> None:
    root = settings.knowledge_dir
    assert root is not None
    body = "Analyze structured incident evidence."
    write_yaml(
        root / "prompts/incident.yaml",
        {
            "entry_type": "prompt",
            "prompt_id": "workflow.incident-diagnosis",
            "version": "1.2.0",
            "status": "stable",
            "required_inputs": ["project_profile", "ai_brief"],
            "output_contract": "incident-diagnosis-v1",
            "body": body,
            "content_sha256": sha256(body.encode()).hexdigest(),
        },
    )
    database = Database(settings.database_path)
    database.initialize()
    index = KnowledgeIndex(database)

    stats = index.rebuild(root)

    assert stats.indexed == 1
    assert stats.quarantined == 0


def test_project_lesson_matches_only_its_project(settings) -> None:
    root = settings.knowledge_dir
    assert root is not None
    write_yaml(
        root / "projects/demo/lessons/log-rotation.yaml",
        {
            "entry_type": "lesson",
            "lesson_id": "LESSON-LOG-0001",
            "scope": "project",
            "project_id": "demo",
            "status": "PROJECT_APPROVED",
            "title": "Reset cursors on inode changes",
            "summary": "Log rotation must compare device and inode before reading.",
            "applicability": {"technologies": ["python"]},
            "verification_refs": ["incident://inc-1"],
            "counterexamples": ["Streams without filesystem inodes"],
            "review_after": "2027-01-01",
            "generated_by_ai": False,
            "fingerprints": ["fp-log-rotation"],
        },
    )
    database = Database(settings.database_path)
    database.initialize()
    index = KnowledgeIndex(database)
    index.rebuild(root)

    demo = ProjectConfig(project_id="demo", display_name="Demo", technologies=("python",))
    other = ProjectConfig(project_id="other", display_name="Other", technologies=("python",))

    assert [item.entry_id for item in index.match(demo, "fp-log-rotation").items] == [
        "LESSON-LOG-0001"
    ]
    assert index.match(other, "fp-log-rotation").items == ()


def test_shared_lesson_requires_approved_evidence_and_counterexamples(settings) -> None:
    root = settings.knowledge_dir
    assert root is not None
    write_yaml(
        root / "shared/lessons/unsafe.yaml",
        {
            "entry_type": "lesson",
            "lesson_id": "LESSON-SHARED-0001",
            "scope": "shared",
            "status": "SHARED_APPROVED",
            "title": "Unsafe shared lesson",
            "summary": "Missing evidence must quarantine this entry.",
            "applicability": {"technologies": ["python"]},
            "verification_refs": [],
            "counterexamples": [],
            "review_after": "2027-01-01",
            "generated_by_ai": True,
        },
    )
    database = Database(settings.database_path)
    database.initialize()
    index = KnowledgeIndex(database)

    stats = index.rebuild(root)

    assert stats.indexed == 0
    assert stats.quarantined == 1
    assert index.list_quarantine()[0].source_path.endswith("unsafe.yaml")


def test_technology_filter_prevents_cross_stack_injection(settings) -> None:
    root = settings.knowledge_dir
    assert root is not None
    write_yaml(
        root / "shared/lessons/python.yaml",
        {
            "entry_type": "lesson",
            "lesson_id": "LESSON-PY-0001",
            "scope": "shared",
            "status": "SHARED_APPROVED",
            "title": "Python import isolation",
            "summary": "Set an explicit package root in isolated test environments.",
            "applicability": {"technologies": ["python"]},
            "verification_refs": ["test://knowledge/python"],
            "counterexamples": ["Rust cargo workspaces"],
            "review_after": "2027-01-01",
            "generated_by_ai": False,
            "fingerprints": ["fp-import"],
        },
    )
    database = Database(settings.database_path)
    database.initialize()
    index = KnowledgeIndex(database)
    index.rebuild(root)

    rust = ProjectConfig(project_id="rust-app", display_name="Rust", technologies=("rust",))

    assert index.match(rust, "fp-import").items == ()


def test_exact_fingerprint_ranks_above_full_text_match(settings) -> None:
    root = settings.knowledge_dir
    assert root is not None
    common = {
        "entry_type": "known_problem",
        "project_id": "demo",
        "technologies": ["python"],
        "verification_refs": ["test://problem"],
    }
    write_yaml(
        root / "projects/demo/known-problems/exact.yaml",
        {
            **common,
            "problem_id": "PROBLEM-EXACT",
            "title": "Database timeout",
            "summary": "Connection pool timeout during import.",
            "fingerprints": ["fp-database-timeout"],
        },
    )
    write_yaml(
        root / "projects/demo/known-problems/text.yaml",
        {
            **common,
            "problem_id": "PROBLEM-TEXT",
            "title": "Database timeout guidance",
            "summary": "General database timeout troubleshooting.",
            "fingerprints": [],
        },
    )
    database = Database(settings.database_path)
    database.initialize()
    index = KnowledgeIndex(database)
    index.rebuild(root)
    project = ProjectConfig(project_id="demo", display_name="Demo", technologies=("python",))

    matches = index.match(
        project,
        "fp-database-timeout",
        query="database timeout",
        limit=10,
    )

    assert [item.entry_id for item in matches.items] == ["PROBLEM-EXACT", "PROBLEM-TEXT"]
    assert matches.items[0].score > matches.items[1].score


def test_markdown_prompt_uses_frontmatter_and_body_hash(settings) -> None:
    root = settings.knowledge_dir
    assert root is not None
    body = "Diagnose the incident from structured evidence.\n"
    metadata = {
        "entry_type": "prompt",
        "prompt_id": "workflow.markdown-diagnosis",
        "version": "1.0.0",
        "status": "stable",
        "required_inputs": ["ai_brief"],
        "output_contract": "incident-diagnosis-v1",
        "content_sha256": sha256(body.encode()).hexdigest(),
    }
    path = root / "prompts/markdown.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
        + "---\n"
        + body,
        encoding="utf-8",
    )
    database = Database(settings.database_path)
    database.initialize()
    index = KnowledgeIndex(database)

    stats = index.rebuild(root)

    assert stats.indexed == 1
    assert stats.quarantined == 0
