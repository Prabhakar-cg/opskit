"""Documented, fixed XML <-> dict mapping convention (research R3).

- An element's child elements become dict keys; a repeated child tag becomes a list.
- Attributes are collected under a reserved ``"@attributes"`` key.
- Direct text content is stored under a reserved ``"#text"`` key, present only when the
  element has non-whitespace text *alongside* attributes/children.
- An element with only text and no attributes/children collapses to that text value directly.
- Tag/attribute names keep their namespace in Clark notation (``{uri}local``) verbatim.

Parsing itself (XXE-safety) is owned by :mod:`opskit.file.formats`, which uses
``defusedxml.ElementTree``; this module only maps between an already-parsed
``xml.etree.ElementTree.Element`` tree and plain Python data, and back.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import cast

_ATTRIBUTES_KEY = "@attributes"
_TEXT_KEY = "#text"


def xml_to_data(element: ET.Element) -> tuple[object, bool]:
    """Convert a parsed XML element tree into plain data.

    Returns:
        ``(data, lossless)`` — ``lossless`` is ``False`` when the source used mixed content
        (text interleaved with child elements), which this convention cannot represent
        (FR-018).
    """
    children = list(element)
    attrs = dict(element.attrib)
    own_text = (element.text or "").strip()
    lossless = True

    if children:
        if own_text:
            lossless = False  # text before/among children = mixed content
        child_data: dict[str, object] = {}
        for child in children:
            if (child.tail or "").strip():
                lossless = False  # text between sibling elements = mixed content
            value, child_lossless = xml_to_data(child)
            lossless = lossless and child_lossless
            if child.tag in child_data:
                existing = child_data[child.tag]
                if isinstance(existing, list):
                    cast("list[object]", existing).append(value)
                else:
                    child_data[child.tag] = [existing, value]
            else:
                child_data[child.tag] = value
        if attrs:
            child_data[_ATTRIBUTES_KEY] = attrs
        return child_data, lossless

    if attrs:
        leaf: dict[str, object] = {_ATTRIBUTES_KEY: attrs}
        if own_text:
            leaf[_TEXT_KEY] = own_text
        return leaf, lossless

    return (own_text if own_text else None), lossless


def data_to_xml(data: object, root_tag: str) -> ET.Element:
    """Build an XML element tree from plain data, inverting :func:`xml_to_data`.

    Only ever *builds* a tree (``Element``/``SubElement``) — never parses untrusted bytes,
    so this has no XXE surface (Bandit S314 targets parsing, not construction).
    """
    element = ET.Element(root_tag)
    if isinstance(data, dict):
        mapping = cast("dict[object, object]", data)
        attrs = mapping.get(_ATTRIBUTES_KEY)
        if isinstance(attrs, dict):
            for name, value in cast("dict[object, object]", attrs).items():
                element.set(str(name), str(value))
        text = mapping.get(_TEXT_KEY)
        if text is not None:
            element.text = str(text)
        for key, value in mapping.items():
            if key in (_ATTRIBUTES_KEY, _TEXT_KEY):
                continue
            tag = str(key)
            if isinstance(value, list):
                for item in cast("list[object]", value):
                    element.append(data_to_xml(item, tag))
            else:
                element.append(data_to_xml(value, tag))
    elif isinstance(data, list):
        # A bare repeated value with no wrapping dict has no tag to reuse per item;
        # represented as a single joined text node rather than dropped silently.
        element.text = ",".join(str(item) for item in cast("list[object]", data))
    elif data is not None:
        element.text = str(data)
    return element
