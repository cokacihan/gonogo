from gonogo.stats import write_readme


def test_generated_blocks_are_replaced_wholesale(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("a\n<!-- gonogo:x -->\nold 1\nold 2\n<!-- /gonogo:x -->\nb\n<!-- gonogo:y --><!-- /gonogo:y -->\n",
                      encoding="utf-8")
    done = write_readme(readme, {"x": "new", "y": "| t |", "z": "unused"})
    text = readme.read_text(encoding="utf-8")
    assert done == ["x", "y"]
    assert "old" not in text and "<!-- gonogo:x -->\nnew\n<!-- /gonogo:x -->" in text and "| t |" in text
