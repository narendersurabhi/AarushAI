"""Retrieve handler - assembles RAG context from job description, resumes, and feedback."""
from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass
from typing import Dict, List

import boto3
from botocore.exceptions import BotoCoreError, ClientError

VECTOR_COLLECTION = os.getenv("VECTOR_COLLECTION_NAME", "resume-tailor-vectors")
VECTOR_INDEX = os.getenv("VECTOR_INDEX_NAME", "resume-tailor-index")
FEEDBACK_TABLE_NAME = os.getenv("FEEDBACK_TABLE_NAME", "")


def lambda_handler(event: Dict, _context) -> Dict:
    if "parsed" not in event or "embedding" not in event:
        raise ValueError("Event must include parsed data and embedding metadata")

    embedder = BedrockEmbedder(boto3.client("bedrock-runtime"))
    vector_client = VectorQueryClient(
        client=boto3.client("opensearchserverless"),
        collection_name=VECTOR_COLLECTION,
        index_name=VECTOR_INDEX,
    )
    feedback_repo = FeedbackRepository(boto3.client("dynamodb"), FEEDBACK_TABLE_NAME)
    retrieval = RetrievalEngine(embedder, vector_client, feedback_repo)

    retrieval_payload = retrieval.build_context(event)
    return {**event, "retrieval": retrieval_payload}


@dataclass
class RetrievalChunk:
    score: float
    text: str
    metadata: Dict[str, str]


class RetrievalEngine:
    def __init__(self, embedder: "BedrockEmbedder", vector_client: "VectorQueryClient", feedback_repo: "FeedbackRepository"):
        self.embedder = embedder
        self.vector_client = vector_client
        self.feedback_repo = feedback_repo

    def build_context(self, event: Dict) -> Dict:
        tenant_id = event["tenantId"]
        job_id = event["jobId"]
        parsed = event["parsed"]
        job_doc = parsed.get("jobDescription", {})
        base_resume = parsed.get("baseResume", {})

        base_experiences = [exp.get("achievements", []) for exp in base_resume.get("experience", [])]
        flattened_experience = "\n".join([bullet for group in base_experiences for bullet in group])
        query_texts = [
            job_doc.get("summary", ""),
            "\n".join(job_doc.get("requirements", [])),
            flattened_experience,
        ]
        queries = self.embedder.embed(query_texts)

        search_results: List[RetrievalChunk] = []
        for query_vector in queries:
            hits = self.vector_client.search(query_vector, top_k=10)
            for hit in hits:
                search_results.append(RetrievalChunk(score=hit["score"], text=hit["text"], metadata=hit["metadata"]))

        feedback = self.feedback_repo.get_feedback(tenant_id, job_id)
        for item in feedback:
            search_results.append(RetrievalChunk(score=item.get("score", 0.8), text=item.get("text", ""), metadata=item))

        # Deduplicate by text and take best score
        deduped: Dict[str, RetrievalChunk] = {}
        for chunk in search_results:
            existing = deduped.get(chunk.text)
            if not existing or existing.score < chunk.score:
                deduped[chunk.text] = chunk

        top_chunks = sorted(deduped.values(), key=lambda chunk: chunk.score, reverse=True)[:20]
        coverage_metrics = CoverageScorer.compute(job_doc, top_chunks)

        return {
            "chunks": [
                {
                    "text": chunk.text,
                    "score": round(chunk.score, 4),
                    "metadata": chunk.metadata,
                }
                for chunk in top_chunks
            ],
            "coverage": coverage_metrics,
            "timestamp": time.time(),
        }


class BedrockEmbedder:
    def __init__(self, runtime_client, model_id: str = "amazon.titan-embed-text-v1"):
        self.runtime = runtime_client
        self.model_id = model_id

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            if not text:
                vectors.append([0.0])
                continue
            try:
                response = self.runtime.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps({"inputText": text}),
                    accept="application/json",
                    contentType="application/json",
                )
            except (ClientError, BotoCoreError) as exc:
                raise RuntimeError(f"Failed to embed query: {exc}") from exc
            payload = json.loads(response.get("body", "{}")) if isinstance(response, dict) else json.loads(response.read())
            vectors.append(payload.get("embedding") or payload.get("vector"))
        return vectors


class VectorQueryClient:
    def __init__(self, client, collection_name: str, index_name: str):
        self.client = client
        self.collection = collection_name
        self.index = index_name

    def search(self, vector: List[float], top_k: int) -> List[Dict]:
        try:
            response = self.client.search(
                collectionName=self.collection,
                indexName=self.index,
                body=json.dumps({
                    "knn": {
                        "field": "embedding",
                        "query_vector": vector,
                        "k": top_k,
                    }
                }),
            )
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(f"Vector search failed: {exc}") from exc
        hits = []
        for doc in response.get("hits", {}).get("hits", []):
            source = doc.get("_source", {})
            hits.append(
                {
                    "score": float(doc.get("_score", 0.0)),
                    "text": source.get("text", ""),
                    "metadata": source.get("metadata", {}),
                }
            )
        return hits


class FeedbackRepository:
    def __init__(self, dynamodb_client, table_name: str):
        self.client = dynamodb_client
        self.table_name = table_name

    def get_feedback(self, tenant_id: str, job_id: str) -> List[Dict]:
        if not self.table_name:
            return []
        try:
            response = self.client.query(
                TableName=self.table_name,
                KeyConditionExpression="tenantId = :tenant and begins_with(feedbackId, :job)",
                ExpressionAttributeValues={
                    ":tenant": {"S": tenant_id},
                    ":job": {"S": f"{job_id}#"},
                },
            )
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(f"Failed to read feedback: {exc}") from exc
        items = response.get("Items", [])
        results: List[Dict] = []
        for item in items:
            results.append(
                {
                    "feedbackId": item.get("feedbackId", {}).get("S"),
                    "text": item.get("text", {}).get("S", ""),
                    "score": float(item.get("score", {}).get("N", "0")),
                    "tags": item.get("tags", {}).get("SS", []),
                }
            )
        return results


class CoverageScorer:
    @staticmethod
    def compute(job_doc: Dict, chunks: List[RetrievalChunk]) -> Dict:
        if not chunks:
            return {"requirements": 0.0, "responsibilities": 0.0, "skills": 0.0}
        def _score(items: List[str]) -> float:
            if not items:
                return 1.0
            matches = 0
            for item in items:
                normalized = item.lower()
                if any(normalized in chunk.text.lower() for chunk in chunks):
                    matches += 1
            return round(matches / len(items), 3)

        return {
            "requirements": _score(job_doc.get("requirements", [])),
            "responsibilities": _score(job_doc.get("responsibilities", [])),
            "skills": _score(job_doc.get("skills", [])),
            "mean": round(statistics.mean([
                _score(job_doc.get("requirements", [])),
                _score(job_doc.get("responsibilities", [])),
                _score(job_doc.get("skills", [])),
            ]), 3),
        }
