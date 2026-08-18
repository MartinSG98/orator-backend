"""DynamoDB implementation of the repository, per ADR 0007.

Single-table design. Every entity is one item keyed PK="{TYPE}#{id}",
SK="META", so gets by id are GetItem calls. GSI1 lists children by parent
(translations by document, jobs by translation), GSI2 lists whole
collections newest first (documents, jobs), both sorted by a zero-padded id.

Integer ids survive the move from SQLite: an atomic counter item per entity
type hands them out, so the API contract keeps its numeric ids.

Document items carry denormalised translation_count and audio_count,
maintained at the mutation points, which makes the list view a single
query instead of a fan-out.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from app.models import Document, DocumentSummary, SynthesisJob, Translation
from app.repository import Repository
from app.services import aws


def _sortable(entity_id: int) -> str:
    return f"{entity_id:012d}"


class DynamoRepository(Repository):
    def __init__(self, table_name: str) -> None:
        if not table_name:
            raise RuntimeError("aws runtime requires ORATOR_TABLE")
        self._table = aws.resource("dynamodb").Table(table_name)

    # Plumbing

    def _next_id(self, kind: str) -> int:
        response = self._table.update_item(
            Key={"PK": "COUNTER", "SK": kind},
            UpdateExpression="ADD #v :one",
            ExpressionAttributeNames={"#v": "value"},
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        return int(response["Attributes"]["value"])

    @staticmethod
    def _serialize(model: Any) -> dict[str, Any]:
        item: dict[str, Any] = {}
        for key, value in model.model_dump().items():
            if isinstance(value, datetime):
                item[key] = value.isoformat()
            elif isinstance(value, float):
                item[key] = Decimal(str(value))
            else:
                item[key] = value
        return item

    @staticmethod
    def _validate(model_cls: type, item: dict[str, Any]) -> Any:
        data = {k: v for k, v in item.items() if k in model_cls.model_fields}
        return model_cls.model_validate(data)

    def _get_item(self, pk: str) -> dict[str, Any] | None:
        return self._table.get_item(Key={"PK": pk, "SK": "META"}).get("Item")

    def _query_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            response = self._table.query(**kwargs)
            items.extend(response["Items"])
            last = response.get("LastEvaluatedKey")
            if last is None:
                return items
            kwargs["ExclusiveStartKey"] = last

    def _bump_document_counter(self, document_id: int, field: str, delta: int) -> None:
        self._table.update_item(
            Key={"PK": f"DOC#{document_id}", "SK": "META"},
            UpdateExpression="ADD #f :d",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":d": delta},
        )

    # Documents

    def add_document(self, document: Document) -> Document:
        document.id = self._next_id("document")
        item = self._serialize(document) | {
            "PK": f"DOC#{document.id}",
            "SK": "META",
            "GSI2PK": "DOCS",
            "GSI2SK": _sortable(document.id),
            "translation_count": 0,
            "audio_count": 0,
        }
        self._table.put_item(Item=item)
        return document

    def get_document(self, document_id: int) -> Document | None:
        item = self._get_item(f"DOC#{document_id}")
        return self._validate(Document, item) if item else None

    def list_documents(self) -> list[DocumentSummary]:
        items = self._query_all(
            IndexName="GSI2",
            KeyConditionExpression=Key("GSI2PK").eq("DOCS"),
            ScanIndexForward=False,
        )
        return [self._validate(DocumentSummary, item) for item in items]

    def delete_document(self, document_id: int) -> None:
        self._table.delete_item(Key={"PK": f"DOC#{document_id}", "SK": "META"})

    # Translations

    def add_translation(self, translation: Translation) -> Translation:
        translation.id = self._next_id("translation")
        item = self._serialize(translation) | {
            "PK": f"TR#{translation.id}",
            "SK": "META",
            "GSI1PK": f"DOC#{translation.document_id}",
            "GSI1SK": _sortable(translation.id),
        }
        self._table.put_item(Item=item)
        self._bump_document_counter(translation.document_id, "translation_count", 1)
        return translation

    def get_translation(self, translation_id: int) -> Translation | None:
        item = self._get_item(f"TR#{translation_id}")
        return self._validate(Translation, item) if item else None

    def find_translation(
        self, document_id: int, language_code: str
    ) -> Translation | None:
        items = self._query_all(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"DOC#{document_id}"),
            FilterExpression=Attr("language_code").eq(language_code),
        )
        return self._validate(Translation, items[0]) if items else None

    def list_translations(self, document_id: int) -> list[Translation]:
        items = self._query_all(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"DOC#{document_id}"),
            ScanIndexForward=False,
        )
        return [self._validate(Translation, item) for item in items]

    def save_translation(self, translation: Translation) -> Translation:
        old = self._get_item(f"TR#{translation.id}")
        if old is None:
            raise RuntimeError(f"translation {translation.id} does not exist")
        self._table.put_item(Item=old | self._serialize(translation))
        return translation

    def delete_translation(self, translation_id: int) -> None:
        old = self._get_item(f"TR#{translation_id}")
        if old is None:
            return
        self._table.delete_item(Key={"PK": f"TR#{translation_id}", "SK": "META"})
        self._bump_document_counter(int(old["document_id"]), "translation_count", -1)

    # Synthesis jobs

    def add_job(self, job: SynthesisJob) -> SynthesisJob:
        job.id = self._next_id("job")
        translation = self._get_item(f"TR#{job.translation_id}")
        item = self._serialize(job) | {
            "PK": f"JOB#{job.id}",
            "SK": "META",
            "GSI1PK": f"TR#{job.translation_id}",
            "GSI1SK": _sortable(job.id),
            "GSI2PK": "JOBS",
            "GSI2SK": _sortable(job.id),
            # denormalised so completed-audio counters know their document
            "document_id": int(translation["document_id"]) if translation else None,
        }
        self._table.put_item(Item=item)
        return job

    def get_job(self, job_id: int) -> SynthesisJob | None:
        item = self._get_item(f"JOB#{job_id}")
        return self._validate(SynthesisJob, item) if item else None

    def list_jobs(self, translation_id: int | None = None) -> list[SynthesisJob]:
        if translation_id is None:
            items = self._query_all(
                IndexName="GSI2",
                KeyConditionExpression=Key("GSI2PK").eq("JOBS"),
                ScanIndexForward=False,
            )
        else:
            items = self._query_all(
                IndexName="GSI1",
                KeyConditionExpression=Key("GSI1PK").eq(f"TR#{translation_id}"),
                ScanIndexForward=False,
            )
        return [self._validate(SynthesisJob, item) for item in items]

    def save_job(self, job: SynthesisJob) -> SynthesisJob:
        old = self._get_item(f"JOB#{job.id}")
        if old is None:
            raise RuntimeError(f"job {job.id} does not exist")
        self._table.put_item(Item=old | self._serialize(job))
        became_completed = old.get("status") != "completed" and job.status == "completed"
        if became_completed and old.get("document_id") is not None:
            self._bump_document_counter(int(old["document_id"]), "audio_count", 1)
        return job

    def delete_job(self, job_id: int) -> None:
        old = self._get_item(f"JOB#{job_id}")
        if old is None:
            return
        self._table.delete_item(Key={"PK": f"JOB#{job_id}", "SK": "META"})
        if old.get("status") == "completed" and old.get("document_id") is not None:
            self._bump_document_counter(int(old["document_id"]), "audio_count", -1)
