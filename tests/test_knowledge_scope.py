from pathlib import Path

from project_agent_controller.knowledge.index import KnowledgeIndex
from project_agent_controller.storage.database import Database


def test_repository_readme_and_output_contracts_are_not_index_candidates(
    settings,
) -> None:
    root: Path | None = settings.knowledge_dir
    assert root is not None
    (root / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Private knowledge repository\n",
        encoding="utf-8",
    )
    contract = root / "output-contracts/incident-diagnosis-v1.yaml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text("type: object\n", encoding="utf-8")
    database = Database(settings.database_path)
    database.initialize()
    index = KnowledgeIndex(database)

    stats = index.rebuild(root)

    assert stats.indexed == 0
    assert stats.quarantined == 0
