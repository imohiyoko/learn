"""schema/*.schema.json で記録の1行を検査する、標準ライブラリだけの小さな JSON Schema の検査器。

使うキーワードだけを扱う：type・required・properties・additionalProperties・items・enum・
minItems・maxItems・minLength・maxLength・minimum・maximum・exclusiveMinimum・pattern・format（date-time）。
"""
import json
import re
from datetime import datetime
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

TYPES = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load(name):
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def is_datetime(v):
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def errors(value, schema, path="$"):
    """value が schema に合わない箇所を、文字列のリストで返す（合っていれば空）。"""
    out = []
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(TYPES[x](value) for x in ts):
            return [f"{path}: 型が {t} でない"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {schema['enum']} のどれでもない")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path}: 短すぎる")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: 長すぎる")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: 形が {schema['pattern']} でない")
        if schema.get("format") == "date-time" and not is_datetime(value):
            out.append(f"{path}: タイムゾーン付きの日時でない")
    if TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: {schema['minimum']} 未満")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: {schema['maximum']} を超える")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            out.append(f"{path}: {schema['exclusiveMinimum']} 以下")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path}: 要素が少なすぎる")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: 要素が多すぎる")
        if "items" in schema:
            for i, v in enumerate(value):
                out += errors(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}.{k}: ない")
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                out += errors(v, props[k], f"{path}.{k}")
            elif extra is False:
                out.append(f"{path}.{k}: スキーマにない項目")
            elif isinstance(extra, dict):
                out += errors(v, extra, f"{path}.{k}")
    return out
