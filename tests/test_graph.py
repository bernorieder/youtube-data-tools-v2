from __future__ import annotations

from xml.etree import ElementTree

from ytdt.graph import Graph


def test_gdf_output_with_types_and_sanitization():
    graph = Graph(directed=True)
    graph.add_node("a", label='Hello, "World"', count=3)
    graph.add_node("b", label="Plain", count=1)
    graph.add_edge("a", "b")
    gdf = graph.to_gdf()
    lines = gdf.strip().split("\n")
    assert lines[0] == "nodedef>name VARCHAR,label VARCHAR,count INT"
    assert lines[1] == "a,Hello   World ,3"
    assert lines[3] == "edgedef>node1 VARCHAR,node2 VARCHAR,directed BOOLEAN"
    assert lines[4] == "a,b,true"


def test_undirected_edges_accumulate_both_directions():
    graph = Graph(directed=False)
    graph.add_node("a")
    graph.add_node("b")
    graph.add_edge("a", "b", weight=2)
    graph.add_edge("b", "a", weight=3)
    assert graph.edges == {("a", "b"): 5}
    assert "a,b,5,false" in graph.to_gdf()


def test_dangling_edges_dropped():
    graph = Graph(directed=True)
    graph.add_node("a")
    graph.add_edge("a", "ghost")
    assert "ghost" not in graph.to_gdf()
    assert "ghost" in graph.to_gdf(drop_dangling=False)


def test_weight_column_only_when_weighted():
    graph = Graph(directed=True)
    graph.add_node("a")
    graph.add_node("b")
    graph.add_edge("a", "b")
    assert "weight" not in graph.to_gdf()
    graph.add_edge("a", "b", weight=1)
    assert "weight INT" in graph.to_gdf()


def test_int_column_exceeding_int32_typed_double():
    graph = Graph()
    graph.add_node("a", viewCount=200_000_000_000, subs=5)
    assert "viewCount DOUBLE" in graph.to_gdf()
    assert "subs INT" in graph.to_gdf()


def test_write_gdf(tmp_path):
    graph = Graph()
    graph.add_node("a", label="x")
    path = graph.write_gdf(tmp_path / "net.gdf")
    assert path.read_text().startswith("nodedef>")


def test_gexf_output_types_labels_and_weights():
    graph = Graph(directed=True)
    graph.add_node("UC1", label="Channel, one", subs=5, views=3_000_000_000, note="a\x00b")
    graph.add_node("UC2", subs=7)
    graph.add_edge("UC1", "UC2", weight=3)
    gexf = graph.to_gexf()

    root = ElementTree.fromstring(gexf)
    ns = {"g": "http://gexf.net/1.3"}
    attributes = {
        a.get("title"): a.get("type") for a in root.findall(".//g:attribute", ns)
    }
    # label is the GEXF node label, not a declared attribute
    assert attributes == {"subs": "long", "views": "long", "note": "string"}

    nodes = {n.get("id"): n for n in root.findall(".//g:node", ns)}
    assert nodes["UC1"].get("label") == "Channel, one"
    assert nodes["UC2"].get("label") == "UC2"  # falls back to the id
    values = {
        v.get("for"): v.get("value") for v in nodes["UC1"].findall(".//g:attvalue", ns)
    }
    assert "3000000000" in values.values()
    assert "ab" in values.values()  # control characters stripped

    edge = root.find(".//g:edge", ns)
    assert (edge.get("source"), edge.get("target"), edge.get("weight")) == ("UC1", "UC2", "3")
    assert root.find(".//g:graph", ns).get("defaultedgetype") == "directed"


def test_gexf_drops_dangling_edges():
    graph = Graph()
    graph.add_node("a")
    graph.add_edge("a", "ghost")
    assert "ghost" not in graph.to_gexf()
    assert "ghost" in graph.to_gexf(drop_dangling=False)


def test_write_gexf(tmp_path):
    graph = Graph()
    graph.add_node("a", label="A")
    path = graph.write_gexf(tmp_path / "net.gexf")
    assert path.read_text(encoding="utf-8").startswith("<?xml")
