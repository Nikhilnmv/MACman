"""A local `@tool` decorator — docstring in, JSON schema out.

Replaces the Anthropic SDK's `@beta_tool` for defining MACman's primitives.
The behaviour is the same: read a function's signature and docstring, produce
`{name, description, input_schema}`, and expose `.call(args)`.

## Why not just use the SDK's

Because the free tier does not otherwise need it. Importing `anthropic` to
define local tools pulled in up to **40 transitive packages** — `boto3`,
`botocore`, `aiohttp` among them — for a docstring parser.

That matters more than install size. Every dependency inherits whatever
permissions MACman has, up to and including Full Disk Access, so each one is
supply-chain surface on a security-sensitive tool. And an assistant whose
pitch is "works offline, no API key" should not require a cloud vendor's SDK
to describe a function that counts files.

The Anthropic SDK is still used by the cloud engine, where it belongs and
where it earns its weight. It is now an optional extra rather than a
requirement for running MACman at all.

## Format

```python
@tool
def count_files(folder: str, extension: str = "") -> str:
    '''Count files in a folder.

    Args:
        folder: Folder to count in.
        extension: Extension without the dot. Omit to count all.
    '''
```

Parameters without defaults are required. The summary line and any prose
before `Args:` become the description the model sees.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable

#: Python annotation → JSON Schema type. Anything unrecognised becomes a
#: string, which is the safe default: the model supplies text, and the tool
#: validates it anyway.
_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}

_ARGS_HEADING = re.compile(r"^\s*(Args|Arguments|Parameters):\s*$", re.M)
_ARG_LINE = re.compile(r"^\s{4,}(\w+)\s*:\s*(.+)$")


def _split_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Return the description and a name → description map for parameters.

    Continuation lines are folded into the preceding parameter, so a
    description wrapped across three lines reads as one sentence rather than
    being truncated at the first newline.
    """
    if not doc:
        return "", {}

    text = inspect.cleandoc(doc)
    match = _ARGS_HEADING.search(text)
    if match is None:
        return text.strip(), {}

    description = text[:match.start()].strip()
    params: dict[str, str] = {}
    current: str | None = None

    for line in text[match.end():].splitlines():
        if not line.strip():
            continue
        # A less-indented line ends the Args block (Returns:, Raises:, …).
        if line.strip().endswith(":") and not line.startswith(" " * 4):
            break
        found = _ARG_LINE.match(line)
        if found:
            current = found.group(1)
            params[current] = found.group(2).strip()
        elif current:
            params[current] = f"{params[current]} {line.strip()}"

    return description, params


@dataclass
class Tool:
    """A callable with a JSON schema, matching the shape the engines expect."""

    name: str
    description: str
    input_schema: dict[str, Any]
    function: Callable[..., Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def call(self, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke with a dict of arguments, ignoring any the tool doesn't take.

        Unknown keys are dropped rather than raising: a model that invents an
        extra field should get the tool run correctly, not a crash.
        """
        supplied = arguments or {}
        accepted = self.input_schema["properties"].keys()
        return self.function(**{k: v for k, v in supplied.items() if k in accepted})

    def __call__(self, *args, **kwargs):
        return self.function(*args, **kwargs)


def tool(function: Callable[..., Any]) -> Tool:
    """Turn a documented function into a `Tool`."""
    description, parameter_docs = _split_docstring(function.__doc__ or "")
    signature = inspect.signature(function)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        annotation = (parameter.annotation
                      if parameter.annotation is not inspect.Parameter.empty else str)
        entry: dict[str, Any] = {
            "type": _JSON_TYPES.get(annotation, "string"),
            "title": name.replace("_", " ").title(),
        }
        if name in parameter_docs:
            entry["description"] = parameter_docs[name]
        if parameter.default is not inspect.Parameter.empty:
            entry["default"] = parameter.default
        else:
            required.append(name)
        properties[name] = entry

    return Tool(
        name=function.__name__,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        function=function,
    )
