"""Tier 3 — Accessibility tree query and control.

The key upgrade over screenshot-and-click (DESIGN.md §5). Two properties matter:

* **The model reads the UI as text.** `query` returns a labelled tree, so the
  model knows a button exists and what it says, rather than inferring it from
  pixels.
* **Elements are addressed by path, not coordinate.** Resolution changes, window
  moves, and theme changes don't invalidate a path. This is what replaces
  FaceTimeOS's `pyautogui.click(93, 928)`.

Requires Accessibility permission, granted to the *responsible process* — the
app that launched Python, not Python itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from AppKit import NSWorkspace
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
)

# Attribute and action names are plain strings in the AX API; using literals
# avoids depending on which PyObjC submodule exports each constant.
_ROLE = "AXRole"
_CHILDREN = "AXChildren"
_ACTION_PRESS = "AXPress"

#: Attributes tried in order to give an element a human-readable label.
_LABEL_ATTRS = ("AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue", "AXHelp")

#: Roles a user can act on. Used by `interactive_only` to cut trees that are
#: otherwise dominated by layout groups.
_INTERACTIVE_ROLES = frozenset({
    "AXButton", "AXMenuItem", "AXMenuBarItem", "AXCheckBox", "AXRadioButton",
    "AXTextField", "AXTextArea", "AXPopUpButton", "AXLink", "AXSlider",
    "AXComboBox", "AXMenuButton", "AXDisclosureTriangle", "AXSegmentedControl",
    "AXIncrementor", "AXTab", "AXSearchField", "AXStepper",
})

#: AX errors worth translating; the rest are reported by number.
_AX_ERRORS: dict[int, str] = {
    -25211: "Accessibility API is disabled for this process.",
    -25202: "The element no longer exists — the UI changed since it was queried.",
    -25204: "The app could not complete the request (busy, or not scriptable).",
    -25205: "The element does not support that attribute.",
    -25212: "The element has no value for that attribute.",
    -25208: "The app has not implemented that part of the Accessibility API.",
}

#: Guards against pathological trees. Some apps expose thousands of siblings.
MAX_CHILDREN_PER_NODE = 100
DEFAULT_MAX_DEPTH = 8


class AccessibilityError(RuntimeError):
    """Raised when the AX API is unavailable or a request fails."""


@dataclass(frozen=True)
class Element:
    path: str
    role: str
    label: str | None
    enabled: bool


def ensure_trusted() -> None:
    """Fail early and actionably if Accessibility isn't granted.

    Without this the AX calls return empty trees rather than errors, which is a
    confusing way to discover a permission problem.
    """
    if not AXIsProcessTrusted():
        raise AccessibilityError(
            "Accessibility permission is not granted to the process running MACman. "
            "Grant it under System Settings → Privacy & Security → Accessibility. "
            "Note the grant attaches to the app that launched Python, not to Python."
        )


def _attr(element: Any, name: str) -> Any:
    """Read one AX attribute, or None if unsupported or empty."""
    error, value = AXUIElementCopyAttributeValue(element, name, None)
    return None if error else value


def _label_of(element: Any) -> str | None:
    for name in _LABEL_ATTRS:
        value = _attr(element, name)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return None


def _app_element(app_name: str) -> Any:
    """AX handle for a running app, matched on name or bundle identifier."""
    needle = app_name.casefold()
    for running in NSWorkspace.sharedWorkspace().runningApplications():
        name = (running.localizedName() or "").casefold()
        bundle = (running.bundleIdentifier() or "").casefold()
        if needle == name or needle == bundle or needle in name:
            return AXUIElementCreateApplication(running.processIdentifier())
    raise AccessibilityError(
        f"No running application matches {app_name!r}. Launch it first (`open -a {app_name!r}`)."
    )


def _walk(element: Any, path: str, depth: int, max_depth: int,
          interactive_only: bool) -> dict[str, Any] | None:
    """Recursively describe `element` as a plain dict."""
    role = _attr(element, _ROLE) or "AXUnknown"
    node: dict[str, Any] = {"path": path, "role": role}

    if (label := _label_of(element)) is not None:
        node["label"] = label
    if (enabled := _attr(element, "AXEnabled")) is not None:
        node["enabled"] = bool(enabled)

    children = []
    if depth < max_depth:
        raw = _attr(element, _CHILDREN) or []
        for index, child in enumerate(raw[:MAX_CHILDREN_PER_NODE]):
            described = _walk(
                child, f"{path}/{index}" if path else str(index),
                depth + 1, max_depth, interactive_only,
            )
            if described is not None:
                children.append(described)
        if len(raw) > MAX_CHILDREN_PER_NODE:
            node["truncated_children"] = len(raw) - MAX_CHILDREN_PER_NODE

    if children:
        node["children"] = children

    # In interactive mode keep a node only if it can be acted on, or if it leads
    # to something that can be — pruning layout scaffolding without severing
    # the paths that reach real controls.
    if interactive_only and role not in _INTERACTIVE_ROLES and not children:
        return None
    return node


def query(
    app: str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    interactive_only: bool = True,
) -> dict[str, Any]:
    """Return the Accessibility tree for `app`.

    Args:
        app: Application name or bundle identifier; must be running.
        max_depth: Recursion limit. Deep trees cost tokens fast.
        interactive_only: Prune nodes that are neither actionable nor on a path
            to something actionable. Usually what you want.

    Returns:
        A nested dict whose every node carries a `path` usable with `press`.
    """
    ensure_trusted()
    tree = _walk(_app_element(app), "", 0, max_depth, interactive_only)
    return tree or {"path": "", "role": "AXApplication", "children": []}


def resolve(app: str, path: str) -> Any:
    """Resolve a `path` from `query` back to a live AX element."""
    ensure_trusted()
    element = _app_element(app)
    if not path:
        return element

    for step in path.split("/"):
        try:
            index = int(step)
        except ValueError as exc:
            raise AccessibilityError(f"Malformed path {path!r} at segment {step!r}.") from exc

        children = _attr(element, _CHILDREN) or []
        if index >= len(children):
            raise AccessibilityError(
                f"Path {path!r} no longer resolves — segment {step!r} is out of range "
                f"({len(children)} children). The UI changed; re-query it."
            )
        element = children[index]
    return element


def find(app: str, *, role: str | None = None, label: str | None = None,
         max_depth: int = DEFAULT_MAX_DEPTH) -> list[Element]:
    """Search the tree for elements matching `role` and/or a label substring.

    Preferred over hand-walking a `query` result when the model already knows
    what it is looking for — "the Share Screen button" resolves to a path
    without the whole tree entering the context window.
    """
    needle = label.casefold() if label else None
    found: list[Element] = []

    def visit(node: dict[str, Any]) -> None:
        node_label = node.get("label")
        role_ok = role is None or node.get("role") == role
        label_ok = needle is None or (node_label is not None and needle in node_label.casefold())
        if role_ok and label_ok and (role or needle):
            found.append(Element(
                path=node["path"], role=node.get("role", "AXUnknown"),
                label=node_label, enabled=bool(node.get("enabled", True)),
            ))
        for child in node.get("children", ()):
            visit(child)

    visit(query(app, max_depth=max_depth, interactive_only=False))
    return found


def press(app: str, path: str) -> str:
    """Perform the press action on the element at `path`."""
    element = resolve(app, path)
    error = AXUIElementPerformAction(element, _ACTION_PRESS)
    if error:
        raise AccessibilityError(
            _AX_ERRORS.get(error, f"Press failed with AX error {error}.")
        )
    return f"pressed {path}"


def set_value(app: str, path: str, value: str) -> str:
    """Set an element's value — typing into a field without synthesising keys."""
    element = resolve(app, path)
    error = AXUIElementSetAttributeValue(element, "AXValue", value)
    if error:
        raise AccessibilityError(
            _AX_ERRORS.get(error, f"Setting value failed with AX error {error}.")
        )
    return f"set {path}"
