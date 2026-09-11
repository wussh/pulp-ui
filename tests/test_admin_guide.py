from fastapi.testclient import TestClient

from app.main import create_app
from app.markdown_render import render
from tests.helpers import AUTH_HEADERS


class StubClient:
    async def ping(self):
        return True

    async def aclose(self):
        return None

    async def request(self, method, path, **kwargs):
        return {"count": 0, "results": []}


def build_app(settings):
    return create_app(settings, client_factory=lambda: StubClient())


def test_admin_guide_requires_auth(settings):
    with TestClient(build_app(settings)) as client:
        assert client.get("/ui/admin-guide").status_code == 401


def test_admin_guide_renders_every_plugin_section(settings):
    with TestClient(build_app(settings)) as client:
        response = client.get("/ui/admin-guide", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.text
    for section in (
        "Container Registry",
        "RPM",
        "DEB",
        "Python / PyPI",
        "Ansible Galaxy",
        "HuggingFace",
        "Maintenance",
    ):
        assert section in body


def test_admin_guide_contains_no_plaintext_credential(settings):
    with TestClient(build_app(settings)) as client:
        body = client.get("/ui/admin-guide", headers=AUTH_HEADERS).text
    # The guide documents commands, never the credential itself.
    assert "Pulp@D3k4cloud!" not in body
    assert "&lt;admin-password&gt;" in body


def test_admin_guide_escapes_html_in_the_source():
    rendered = render("`<script>alert(1)</script>`\n\nplain <img src=x onerror=y>\n")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "onerror" in rendered  # preserved as inert escaped text


def test_render_handles_headings_code_tables_and_lists():
    rendered = render(
        "# Title\n\n"
        "## Section\n\n"
        "```\ncode line\n```\n\n"
        "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
        "- one\n- two\n\n"
        "1. first\n"
    )
    assert "<h2>Title</h2>" in rendered
    assert "<h3>Section</h3>" in rendered
    assert "<pre><code>code line</code></pre>" in rendered
    assert "<th>a</th>" in rendered and "<td>1</td>" in rendered
    assert "<li>one</li>" in rendered
    assert "<ol>" in rendered
