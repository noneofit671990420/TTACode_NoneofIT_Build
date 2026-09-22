"""Tests for harness.gui.markdown — stdlib only, no Qt needed."""

import unittest

from harness.gui.markdown import render


class MarkdownTests(unittest.TestCase):
    def test_html_is_escaped(self):
        out = render("<script>alert(1)</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_fenced_code_block(self):
        out = render("```python\nprint('hi')\n```")
        self.assertIn('<pre class="code">', out)
        self.assertIn("print(&#x27;hi&#x27;)", out)
        self.assertIn("python", out)

    def test_inline_code_bold_italic(self):
        out = render("Use `run_command` for **speed** and *style*.")
        self.assertIn("<code>run_command</code>", out)
        self.assertIn("<b>speed</b>", out)
        self.assertIn("<i>style</i>", out)

    def test_bullet_and_numbered_lists(self):
        out = render("- one\n- two\n\n1. first\n2. second")
        self.assertIn("<ul>", out)
        self.assertIn("<li>one</li>", out)
        self.assertIn("<ol>", out)
        self.assertIn("<li>first</li>", out)

    def test_heading(self):
        out = render("# Title")
        self.assertIn('class="heading"', out)
        self.assertIn("Title", out)

    def test_code_block_protects_markup(self):
        out = render("```\n**not bold** `not code`\n```")
        self.assertNotIn("<b>not bold</b>", out)
        self.assertIn("**not bold**", out)

    def test_empty(self):
        self.assertEqual(render(""), "")


if __name__ == "__main__":
    unittest.main()
