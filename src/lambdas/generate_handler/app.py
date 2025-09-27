"""Generate handler - orchestrates multi-step prompt chain using Amazon Bedrock."""
from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

PROMPT_FILE = os.getenv(
    "PROMPT_TEMPLATES_PATH",
    os.path.join(os.path.dirname(__file__), "../../../docs/prompts/prompt_templates.json"),
)
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
BEDROCK_GUARDRAIL_ARN = os.getenv("BEDROCK_GUARDRAIL_ARN", "")


def lambda_handler(event: Dict, _context) -> Dict:
    if "parsed" not in event or "retrieval" not in event:
        raise ValueError("Parsed data and retrieval context are required before generation")

    prompt_loader = PromptLoader(PROMPT_FILE)
    bedrock_client = boto3.client("bedrock-runtime")
    generator = BedrockTextGenerator(bedrock_client, BEDROCK_MODEL_ID, BEDROCK_GUARDRAIL_ARN)
    chain = PromptChain(generator, prompt_loader)

    generation = chain.generate(event)

    return {**event, "generation": generation}


class PromptLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._cache: Optional[Dict] = None

    def load(self) -> Dict:
        if self._cache is None:
            path = pathlib.Path(self.file_path).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Prompt template file not found: {path}")
            with path.open("r", encoding="utf-8") as handle:
                self._cache = json.load(handle)
        return self._cache

    def get(self, prompt_name: str) -> Dict:
        prompts = self.load()
        if prompt_name not in prompts:
            raise KeyError(f"Prompt {prompt_name} not defined")
        return prompts[prompt_name]


@dataclass
class PromptResult:
    data: Dict
    raw_response: str
    prompt_name: str


class PromptChain:
    def __init__(self, generator: "BedrockTextGenerator", prompts: PromptLoader):
        self.generator = generator
        self.prompts = prompts

    def generate(self, event: Dict) -> Dict:
        tenant_id = event["tenantId"]
        job_id = event["jobId"]
        parsed = event["parsed"]
        retrieval = event["retrieval"]
        options = event.get("options", {})
        style_profile = None
        if isinstance(parsed.get("styleGuide"), dict):
            style_profile = parsed["styleGuide"].get("profile")

        step1 = self._invoke_json("competency_extraction", {
            "jobDescription": parsed.get("jobDescription", {}),
        })

        step2 = self._invoke_json("experience_alignment", {
            "competencies": step1.data.get("competencies", []),
            "baseResume": parsed.get("baseResume", {}),
            "validatedResumes": parsed.get("validatedResumes", []),
            "retrievedChunks": retrieval.get("chunks", []),
        })

        step3 = self._invoke_json("bullet_rewrite", {
            "alignmentPlan": step2.data,
            "options": {
                "tone": options.get("tone", "professional"),
                "keywords": options.get("keywords", []),
                "styleGuide": style_profile or {},
            },
        })

        step4 = self._invoke_json("skills_harmonization", {
            "extractedSkills": parsed.get("extractedSkills", []),
            "alignmentPlan": step2.data,
            "bulletRewrites": step3.data,
        })

        step5 = self._invoke_json("consistency_check", {
            "jobDescription": parsed.get("jobDescription", {}),
            "baseResume": parsed.get("baseResume", {}),
            "rewrittenBullets": step3.data,
            "skills": step4.data,
            "options": {
                "length": options.get("length", "2 pages"),
                "includeCoverLetter": options.get("includeCoverLetter", False),
                "styleGuide": style_profile or {},
            },
            "styleGuide": style_profile or {},
        })

        tailored_resume = step5.data.get("tailoredResume", {})
        change_log = step5.data.get("changeLog", [])
        cover_letter = step5.data.get("coverLetter") if options.get("includeCoverLetter") else None

        prompt_metadata = [
            {
                "stage": step.prompt_name,
                "model": self.generator.model_id,
                "temperature": self.generator.temperature,
                "maxTokens": self.generator.max_tokens,
                "rawResponse": step.raw_response,
            }
            for step in [step1, step2, step3, step4, step5]
        ]

        return {
            "tailoredResume": tailored_resume,
            "changeLog": change_log,
            "coverLetter": cover_letter,
            "promptMetadata": prompt_metadata,
            "timestamp": time.time(),
        }

    def _invoke_json(self, prompt_name: str, variables: Dict) -> PromptResult:
        prompt = self.prompts.get(prompt_name)
        rendered_prompt = prompt["template"].format(**{k: json.dumps(v, ensure_ascii=False) for k, v in variables.items()})
        response = self.generator.invoke(rendered_prompt, stop_sequences=prompt.get("stopSequences", []))
        try:
            data = json.loads(response)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Prompt {prompt_name} did not return valid JSON: {exc}\nResponse: {response}") from exc
        return PromptResult(data=data, raw_response=response, prompt_name=prompt_name)


class BedrockTextGenerator:
    def __init__(self, runtime_client, model_id: str, guardrail_arn: str = "", temperature: float = 0.3, max_tokens: int = 2000):
        self.runtime = runtime_client
        self.model_id = model_id
        self.guardrail_arn = guardrail_arn
        self.temperature = temperature
        self.max_tokens = max_tokens

    def invoke(self, prompt: str, stop_sequences: Optional[List[str]] = None) -> str:
        body = {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": self.max_tokens,
                "temperature": self.temperature,
            },
        }
        if stop_sequences:
            body["textGenerationConfig"]["stopSequences"] = stop_sequences
        if self.guardrail_arn:
            body["guardrailConfig"] = {"guardrailIdentifier": self.guardrail_arn}
        try:
            response = self.runtime.invoke_model(
                modelId=self.model_id,
                body=json.dumps(body),
                accept="application/json",
                contentType="application/json",
            )
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(f"Bedrock generation failed: {exc}") from exc

        payload = response.get("body") if isinstance(response, dict) else response.read()
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        data = json.loads(payload)
        outputs = data.get("results") or data.get("output") or []
        if outputs and isinstance(outputs, list):
            return outputs[0].get("outputText") or outputs[0].get("text", "")
        if isinstance(data, dict) and "outputText" in data:
            return data["outputText"]
        if isinstance(payload, str):
            return payload
        raise RuntimeError("Unexpected response format from Bedrock")
