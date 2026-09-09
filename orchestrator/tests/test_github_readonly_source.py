import base64
import json
import unittest

from github_readonly_source import GitHubReadOnlySource


class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps(self.value).encode()


class GitHubSourceTest(unittest.TestCase):
    def test_reads_tree_and_blob_with_get_only(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.get_method())
            if "/git/trees/" in request.full_url:
                return Response({"tree": [{"type": "blob", "path": "src/a.py", "sha": "sha1"}]})
            return Response({"content": base64.b64encode(b"print(1)").decode()})
        snapshot = GitHubReadOnlySource("owner/repo", "token", opener).snapshot("a" * 40, ("src/",))
        self.assertEqual(snapshot, b"--- src/a.py\nprint(1)\n")
        self.assertTrue(all(method == "GET" for method in calls))
