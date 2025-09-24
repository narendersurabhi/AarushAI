"""Embed handler - generates Titan embeddings and indexes into OpenSearch Serverless."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

import boto3
from botocore.exceptions import BotoCoreError, ClientError

VECTOR_COLLECTION = os.getenv("VECTOR_COLLECTION_NAME", "resume-tailor-vectors")
VECTOR_INDEX = os.getenv("VECTOR_INDEX_NAME", "resume-tailor-index")


def lambda_handler(event: Dict, _context) -> Dict:
    if "parsed" not in event:
        raise ValueError("Parsed documents are required prior to embedding")

    embedder = BedrockEmbedder(boto3.client("bedrock-runtime"))
    vector_client = VectorStoreClient(
        opensearch_client=boto3.client("opensearchserverless"),
        collection_name=VECTOR_COLLECTION,
        index_name=VECTOR_INDEX,
    )
    orchestrator = EmbeddingOrchestrator(embedder, vector_client)
    embedding_result = orchestrator.process(event)

    enriched_event = {
        **event,
        "embedding": embedding_result,
    }
    return enriched_event


@dataclass
class EmbeddingDocument:
    id: str
    text: str
    metadata: Dict[str, str]


class EmbeddingOrchestrator:
    def __init__(self, embedder: "BedrockEmbedder", vector_client: "VectorStoreClient"):
        self.embedder = embedder
        self.vector_client = vector_client

    def process(self, event: Dict) -> Dict:
        documents = list(DocumentBuilder.build_documents(event))
        texts = [doc.text for doc in documents]
        if not texts:
            raise ValueError("No documents generated for embedding")

        embeddings = self.embedder.embed(texts)
        if len(embeddings) != len(documents):
            raise RuntimeError("Embedding count mismatch")

        vector_ids = self.vector_client.upsert(documents, embeddings)

        return {
            "vectorIds": vector_ids,
            "documentCount": len(documents),
            "index": VECTOR_INDEX,
            "collection": VECTOR_COLLECTION,
            "timestamp": time.time(),
        }


class DocumentBuilder:
    @staticmethod
    def build_documents(event: Dict) -> Iterable[EmbeddingDocument]:
        tenant_id = event["tenantId"]
        job_id = event["jobId"]
        parsed = event["parsed"]
        base = parsed.get("baseResume", {})
        job = parsed.get("jobDescription", {})
        validated = parsed.get("validatedResumes", [])

        yield EmbeddingDocument(
            id=f"{tenant_id}#{job_id}#jd",
            text=_create_chunk(job.get("summary", ""), job.get("requirements", []) + job.get("responsibilities", [])),
            metadata={
                "tenantId": tenant_id,
                "jobId": job_id,
                "type": "job-description",
                "title": job.get("meta", {}).get("title", "unknown"),
            },
        )

        for idx, section in enumerate(base.get("experience", [])):
            yield EmbeddingDocument(
                id=f"{tenant_id}#{job_id}#base#{idx}",
                text=_create_chunk(section.get("title", ""), section.get("achievements", [])),
                metadata={
                    "tenantId": tenant_id,
                    "jobId": job_id,
                    "type": "base-experience",
                    "role": section.get("title", ""),
                    "company": section.get("company", ""),
                },
            )

        for idx, resume in enumerate(validated):
            ach = []
            for exp in resume.get("experience", []):
                ach.extend(exp.get("achievements", []))
            yield EmbeddingDocument(
                id=f"{tenant_id}#{job_id}#validated#{idx}",
                text=_create_chunk(resume.get("summary", ""), ach),
                metadata={
                    "tenantId": tenant_id,
                    "jobId": job_id,
                    "type": "validated-resume",
                    "sourceKey": resume.get("meta", {}).get("sourceKey", f"validated-{idx}"),
                    "outcome": resume.get("meta", {}).get("outcome", "interview"),
                },
            )

        for idx, skill in enumerate(event.get("parsed", {}).get("extractedSkills", [])):
            yield EmbeddingDocument(
                id=f"{tenant_id}#{job_id}#skill#{idx}",
                text=skill.get("skill", ""),
                metadata={
                    "tenantId": tenant_id,
                    "jobId": job_id,
                    "type": "skill",
                    "sources": ",".join(skill.get("sources", [])),
                },
            )


def _create_chunk(header: str, items: Sequence[str]) -> str:
    body = "\n".join(item for item in items if item)
    return f"{header}\n{body}".strip()


class BedrockEmbedder:
    def __init__(self, runtime_client, model_id: str = "amazon.titan-embed-text-v1"):
        self.runtime = runtime_client
        self.model_id = model_id

    def embed(self, texts: List[str]) -> List[List[float]]:
        embeddings: List[List[float]] = []
        for text in texts:
            try:
                response = self.runtime.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps({"inputText": text}),
                    accept="application/json",
                    contentType="application/json",
                )
            except (ClientError, BotoCoreError) as exc:
                raise RuntimeError(f"Failed to invoke Bedrock embedding model: {exc}") from exc
            payload = json.loads(response.get("body", "{}")) if isinstance(response, dict) else json.loads(response.read())
            vector = payload.get("embedding") or payload.get("vector")
            if not vector:
                raise RuntimeError("Embedding response missing vector field")
            embeddings.append(vector)
        return embeddings


class VectorStoreClient:
    def __init__(self, opensearch_client, collection_name: str, index_name: str):
        self.client = opensearch_client
        self.collection_name = collection_name
        self.index_name = index_name

    def upsert(self, documents: List[EmbeddingDocument], embeddings: List[List[float]]) -> List[str]:
        vector_ids: List[str] = []
        for doc, embedding in zip(documents, embeddings):
            doc_id = doc.id or str(uuid.uuid4())
            vector_ids.append(doc_id)
            payload = {
                "id": doc_id,
                "index": self.index_name,
                "collection": self.collection_name,
                "embedding": embedding,
                "metadata": doc.metadata,
                "text": doc.text,
            }
            try:
                self.client.batch_put_document(
                    body=json.dumps({"documents": [payload]}),
                    collectionName=self.collection_name,
                    indexName=self.index_name,
                )
            except (ClientError, BotoCoreError) as exc:
                raise RuntimeError(f"Failed to upsert document {doc_id}: {exc}") from exc
        return vector_ids
