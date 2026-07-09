"""Lightweight network container with GEXF export (Gephi-compatible).

GEXF 1.3 is the primary output format; the older GDF serializer is kept
for callers that still want it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable
from xml.etree.ElementTree import Element, SubElement, indent, tostring

_UNSAFE = re.compile(r"[,\"'\r\n]")

# characters that are invalid in XML 1.0 and must not reach GEXF output
_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Gephi parses GDF INT as 32-bit; larger values (e.g. channel view counts)
# must be typed DOUBLE to survive import.
_INT32_MAX = 2**31 - 1


def _sanitize(value: Any) -> str:
    """Make a value safe for the comma-separated GDF format."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _UNSAFE.sub(" ", value)
    return str(value)


def _xml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return _XML_INVALID.sub("", str(value))


def _gexf_type(values: Iterable[Any]) -> str:
    values = [v for v in values if v is not None and v != ""]
    if values and all(isinstance(v, bool) for v in values):
        return "boolean"
    if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "long"
    if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "double"
    return "string"


def _gdf_type(values: Iterable[Any]) -> str:
    values = [v for v in values if v is not None and v != ""]
    if values and all(isinstance(v, bool) for v in values):
        return "BOOLEAN"
    if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "INT" if all(abs(v) <= _INT32_MAX for v in values) else "DOUBLE"
    if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "DOUBLE"
    return "VARCHAR"


class Graph:
    """Nodes with attributes, edges with optional accumulated weights.

    For undirected graphs, edges are keyed on the sorted node pair so
    (a, b) and (b, a) accumulate into one edge.
    """

    def __init__(self, *, directed: bool = True):
        self.directed = directed
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str], int | None] = {}

    def add_node(self, node_id: str, **attrs: Any) -> None:
        self.nodes.setdefault(node_id, {}).update(attrs)

    def add_edge(self, source: str, target: str, *, weight: int | None = None) -> None:
        key = (source, target) if self.directed else tuple(sorted((source, target)))  # type: ignore[assignment]
        if weight is None:
            self.edges.setdefault(key, None)
        else:
            current = self.edges.get(key) or 0
            self.edges[key] = current + weight

    @property
    def weighted(self) -> bool:
        return any(w is not None for w in self.edges.values())

    def to_gexf(self, *, drop_dangling: bool = True) -> str:
        """Serialize to GEXF 1.3, Gephi's native exchange format.

        A node's ``label`` attribute becomes the GEXF node label (falling
        back to the node id); all other attributes are exported as typed
        ``attvalues``. ``drop_dangling`` removes edges whose endpoints
        lack a node entry.
        """
        field_names: list[str] = []
        for attrs in self.nodes.values():
            for name in attrs:
                if name != "label" and name not in field_names:
                    field_names.append(name)
        types = {
            name: _gexf_type(attrs.get(name) for attrs in self.nodes.values())
            for name in field_names
        }
        indices = {name: str(index) for index, name in enumerate(field_names)}

        gexf = Element("gexf", {"xmlns": "http://gexf.net/1.3", "version": "1.3"})
        meta = SubElement(gexf, "meta")
        SubElement(meta, "creator").text = "YouTube Data Tools"
        graph = SubElement(
            gexf, "graph", {"defaultedgetype": "directed" if self.directed else "undirected"}
        )
        if field_names:
            attributes = SubElement(graph, "attributes", {"class": "node"})
            for name in field_names:
                SubElement(
                    attributes,
                    "attribute",
                    {"id": indices[name], "title": name, "type": types[name]},
                )

        nodes = SubElement(graph, "nodes")
        for node_id, attrs in self.nodes.items():
            node = SubElement(
                nodes,
                "node",
                {"id": _xml_value(node_id), "label": _xml_value(attrs.get("label") or node_id)},
            )
            values = [
                (name, value)
                for name in field_names
                if (value := attrs.get(name)) is not None and value != ""
            ]
            if values:
                attvalues = SubElement(node, "attvalues")
                for name, value in values:
                    SubElement(
                        attvalues,
                        "attvalue",
                        {"for": indices[name], "value": _xml_value(value)},
                    )

        edges = SubElement(graph, "edges")
        weighted = self.weighted
        for edge_id, ((source, target), weight) in enumerate(self.edges.items()):
            if drop_dangling and (source not in self.nodes or target not in self.nodes):
                continue
            attrs = {"id": str(edge_id), "source": _xml_value(source), "target": _xml_value(target)}
            if weighted:
                attrs["weight"] = str(weight if weight is not None else 1)
            SubElement(edges, "edge", attrs)

        indent(gexf)
        return tostring(gexf, encoding="unicode", xml_declaration=True) + "\n"

    def write_gexf(self, path: str | Path, *, drop_dangling: bool = True) -> Path:
        path = Path(path)
        path.write_text(self.to_gexf(drop_dangling=drop_dangling), encoding="utf-8")
        return path

    def to_gdf(self, *, drop_dangling: bool = True) -> str:
        """Serialize to GDF. ``drop_dangling`` removes edges whose endpoints lack a node entry."""
        field_names: list[str] = []
        for attrs in self.nodes.values():
            for name in attrs:
                if name not in field_names:
                    field_names.append(name)

        types = {
            name: _gdf_type(attrs.get(name) for attrs in self.nodes.values())
            for name in field_names
        }
        lines = [
            "nodedef>name VARCHAR"
            + "".join(f",{name} {types[name]}" for name in field_names)
        ]
        for node_id, attrs in self.nodes.items():
            lines.append(
                _sanitize(node_id)
                + "".join("," + _sanitize(attrs.get(name, "")) for name in field_names)
            )

        weighted = self.weighted
        edgedef = "edgedef>node1 VARCHAR,node2 VARCHAR"
        if weighted:
            edgedef += ",weight INT"
        edgedef += ",directed BOOLEAN"
        lines.append(edgedef)
        for (source, target), weight in self.edges.items():
            if drop_dangling and (source not in self.nodes or target not in self.nodes):
                continue
            row = f"{_sanitize(source)},{_sanitize(target)}"
            if weighted:
                row += f",{weight if weight is not None else 1}"
            row += f",{'true' if self.directed else 'false'}"
            lines.append(row)
        return "\n".join(lines) + "\n"

    def write_gdf(self, path: str | Path, *, drop_dangling: bool = True) -> Path:
        path = Path(path)
        path.write_text(self.to_gdf(drop_dangling=drop_dangling), encoding="utf-8")
        return path
