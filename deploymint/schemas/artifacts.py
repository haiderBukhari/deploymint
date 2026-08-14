"""The LLM's (and the template's) output contract. See docs/03-data-model.md §3.3."""

import yaml
from pydantic import BaseModel, Field, field_validator


class GeneratedArtifacts(BaseModel):
    """What the Artifact Smith MUST produce. Enforced before anything is written."""

    dockerfile: str = Field(min_length=20)
    dockerignore: str = ""
    k8s_deployment: str = Field(min_length=20)
    k8s_service: str = Field(min_length=10)
    reasoning: str = ""

    @field_validator("dockerfile")
    @classmethod
    def must_look_like_a_dockerfile(cls, v: str) -> str:
        upper = v.upper()
        if "FROM " not in upper:
            raise ValueError("Dockerfile has no FROM instruction")
        if "```" in v:
            raise ValueError("Dockerfile still contains markdown fences")
        return v.strip() + "\n"

    @field_validator("k8s_deployment", "k8s_service")
    @classmethod
    def must_be_valid_yaml(cls, v: str) -> str:
        try:
            doc = yaml.safe_load(v)
        except yaml.YAMLError as e:
            raise ValueError(f"invalid YAML: {e}") from e
        if not isinstance(doc, dict) or "kind" not in doc:
            raise ValueError("YAML is not a Kubernetes resource (no 'kind')")
        return v.strip() + "\n"
