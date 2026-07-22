from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

import yaml
from pydantic import BaseModel, ConfigDict

from project_agent_controller.curation.redaction import Redactor
from project_agent_controller.domain.models import ProjectConfig
from project_agent_controller.knowledge.models import (
    KnowledgeEntry,
    KnownProblem,
    Lesson,
    LessonStatus,
    PromptMetadata,
    PromptStatus,
)
from project_agent_controller.storage.database import Database


class IndexStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    indexed: int = 0
    quarantined: int = 0


class QuarantineItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_path: str
    reason: str
    content_sha256: str


class KnowledgeMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    entry_type: str
    title: str
    score: int
    source_path: str


class KnowledgeMatches(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[KnowledgeMatch, ...] = ()


class KnowledgeIndex:
    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def rebuild(self, root: Path) -> IndexStats:
        root = root.resolve()
        indexed = 0
        quarantined = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM knowledge_fingerprints")
            connection.execute("DELETE FROM knowledge_entries")
            connection.execute("DELETE FROM knowledge_entries_fts")
            connection.execute("DELETE FROM knowledge_quarantine")
            paths = self._candidate_paths(root)
            for path in paths:
                content = path.read_bytes()
                digest = sha256(content).hexdigest()
                try:
                    text = content.decode("utf-8")
                    redaction = Redactor().redact(text)
                    unsafe = {"authorization", "private_key", "token"}.intersection(
                        redaction.matches
                    )
                    if not redaction.safe_to_export or unsafe:
                        raise ValueError("knowledge file contains unsafe secret material")
                    raw = self._load_document(path, text)
                    entry = self._parse_entry(raw)
                    self._insert_entry(connection, root, path, entry, digest)
                    indexed += 1
                except Exception as error:
                    connection.execute(
                        """
                        INSERT INTO knowledge_quarantine (source_path, reason, content_sha256)
                        VALUES (?, ?, ?)
                        """,
                        (str(path), str(error), digest),
                    )
                    quarantined += 1
            connection.commit()
        return IndexStats(indexed=indexed, quarantined=quarantined)

    def list_quarantine(self) -> tuple[QuarantineItem, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_path, reason, content_sha256
                FROM knowledge_quarantine
                ORDER BY source_path ASC
                """
            ).fetchall()
        return tuple(QuarantineItem.model_validate(dict(row)) for row in rows)

    def match(
        self,
        project: ProjectConfig,
        fingerprint: str,
        *,
        query: str | None = None,
        limit: int = 20,
    ) -> KnowledgeMatches:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        exact_ids: set[str] = set()
        text_ids: set[str] = set()
        with self._connect() as connection:
            exact_rows = connection.execute(
                "SELECT entry_id FROM knowledge_fingerprints WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchall()
            exact_ids = {str(row["entry_id"]) for row in exact_rows}
            if query and query.strip():
                expression = self._fts_expression(query)
                if expression:
                    text_rows = connection.execute(
                        "SELECT entry_id FROM knowledge_entries_fts "
                        "WHERE knowledge_entries_fts MATCH ?",
                        (expression,),
                    ).fetchall()
                    text_ids = {str(row["entry_id"]) for row in text_rows}
            candidate_ids = sorted(exact_ids | text_ids)
            if not candidate_ids:
                return KnowledgeMatches()
            placeholders = ",".join("?" for _ in candidate_ids)
            rows = connection.execute(
                f"SELECT * FROM knowledge_entries WHERE entry_id IN ({placeholders})",
                tuple(candidate_ids),
            ).fetchall()

        matches: list[KnowledgeMatch] = []
        for row in rows:
            entry = self._parse_entry(json.loads(str(row["payload_json"])))
            if not self._is_applicable(entry, project):
                continue
            score = 100 if entry.entry_id in exact_ids else 0
            if entry.entry_id in text_ids:
                score += 20
            if getattr(entry, "project_id", None) == project.project_id:
                score += 10
            matches.append(
                KnowledgeMatch(
                    entry_id=entry.entry_id,
                    entry_type=str(row["entry_type"]),
                    title=str(row["title"]),
                    score=score,
                    source_path=str(row["source_path"]),
                )
            )
        matches.sort(key=lambda item: (-item.score, item.entry_id))
        return KnowledgeMatches(items=tuple(matches[:limit]))

    @staticmethod
    def _candidate_paths(root: Path) -> list[Path]:
        candidates: set[Path] = set()
        roots = [root / "prompts", root / "shared" / "lessons"]
        projects_root = root / "projects"
        if projects_root.exists():
            for project_root in projects_root.iterdir():
                if project_root.is_dir():
                    roots.extend(
                        [
                            project_root / "known-problems",
                            project_root / "lessons",
                        ]
                    )
        for candidate_root in roots:
            if not candidate_root.exists():
                continue
            for suffix in ("*.yaml", "*.yml", "*.md"):
                candidates.update(candidate_root.rglob(suffix))
        return sorted(candidates)

    @staticmethod
    def _load_document(path: Path, text: str) -> dict[str, Any]:
        if path.suffix.lower() != ".md":
            raw = yaml.safe_load(text)
        else:
            if not text.startswith("---\n"):
                raise ValueError("markdown knowledge entry requires YAML frontmatter")
            separator = "\n---\n"
            end = text.find(separator, 4)
            if end < 0:
                raise ValueError("markdown frontmatter is not terminated")
            raw = yaml.safe_load(text[4:end])
            if not isinstance(raw, dict):
                raise ValueError("markdown frontmatter must be a mapping")
            raw = dict(raw)
            raw["body"] = text[end + len(separator) :]
        if not isinstance(raw, dict):
            raise ValueError("knowledge entry must be a mapping")
        return raw

    @staticmethod
    def _parse_entry(raw: dict[str, Any]) -> KnowledgeEntry:
        entry_type = raw.get("entry_type")
        if entry_type == "prompt":
            return PromptMetadata.model_validate(raw)
        if entry_type == "known_problem":
            return KnownProblem.model_validate(raw)
        if entry_type == "lesson":
            return Lesson.model_validate(raw)
        raise ValueError(f"unsupported entry_type: {entry_type!r}")

    @staticmethod
    def _insert_entry(
        connection: sqlite3.Connection,
        root: Path,
        path: Path,
        entry: KnowledgeEntry,
        digest: str,
    ) -> None:
        if isinstance(entry, PromptMetadata):
            project_id = None
            status = entry.status.value
            title = entry.title or entry.prompt_id
            summary = entry.summary
            technologies: tuple[str, ...] = ()
            components: tuple[str, ...] = ()
            workflows: tuple[str, ...] = ()
            risk_tags: tuple[str, ...] = ()
            fingerprints: tuple[str, ...] = ()
        elif isinstance(entry, KnownProblem):
            project_id = entry.project_id
            status = entry.status
            title = entry.title
            summary = entry.summary
            technologies = entry.technologies
            components = entry.components
            workflows = entry.workflows
            risk_tags = entry.risk_tags
            fingerprints = entry.fingerprints
        else:
            project_id = entry.project_id
            status = entry.status.value
            title = entry.title
            summary = entry.summary
            technologies = entry.applicability.technologies
            components = entry.applicability.components
            workflows = entry.applicability.workflows
            risk_tags = entry.applicability.risk_tags
            fingerprints = entry.fingerprints

        source_path = str(path.relative_to(root))
        payload_json = json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO knowledge_entries (
                entry_id, entry_type, project_id, status, title, summary,
                technologies_json, components_json, workflows_json, risk_tags_json,
                payload_json, source_path, content_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.entry_id,
                entry.entry_type,
                project_id,
                status,
                title,
                summary,
                json.dumps(technologies),
                json.dumps(components),
                json.dumps(workflows),
                json.dumps(risk_tags),
                payload_json,
                source_path,
                digest,
            ),
        )
        connection.execute(
            """
            INSERT INTO knowledge_entries_fts (
                entry_id, title, summary, technologies, components, workflows, risk_tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.entry_id,
                title,
                summary,
                " ".join(technologies),
                " ".join(components),
                " ".join(workflows),
                " ".join(risk_tags),
            ),
        )
        for fingerprint in fingerprints:
            connection.execute(
                "INSERT INTO knowledge_fingerprints (entry_id, fingerprint) VALUES (?, ?)",
                (entry.entry_id, fingerprint),
            )

    @staticmethod
    def _is_applicable(entry: KnowledgeEntry, project: ProjectConfig) -> bool:
        project_technologies = set(project.technologies)
        if isinstance(entry, PromptMetadata):
            return entry.status is PromptStatus.STABLE
        if isinstance(entry, KnownProblem):
            if entry.status != "confirmed" or entry.project_id != project.project_id:
                return False
            return not entry.technologies or bool(
                project_technologies.intersection(entry.technologies)
            )
        if entry.status in {LessonStatus.DEPRECATED, LessonStatus.REVOKED}:
            return False
        if entry.scope == "project" and entry.project_id != project.project_id:
            return False
        required = set(entry.applicability.technologies)
        return not required or bool(project_technologies.intersection(required))

    @staticmethod
    def _fts_expression(query: str) -> str:
        tokens = [token for token in query.replace('"', " ").split() if token]
        return " AND ".join(f'"{token}"' for token in tokens)
