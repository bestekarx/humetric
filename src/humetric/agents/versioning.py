import hashlib
import json

from pydantic import BaseModel


def hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def hash_schema(schema_cls: type[BaseModel]) -> str:
    raw = json.dumps(schema_cls.model_json_schema(), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
