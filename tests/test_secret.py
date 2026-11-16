import unittest

from keyleak import report, scan

PATHS = {
    "web/app.py": 'password = "hunter2"\n',
    "ops/deploy.yml": "token: abc\n",
}


class SecretTest(unittest.TestCase):
    def test_password_assignment_is_found(self):
        entries = scan(PATHS)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["value"], "hunter2")

    def test_comment_with_keyword_is_reported(self):
        entries = scan({"web/app.py": "# password = old\n"})["entries"]
        self.assertEqual(len(entries), 1)

    def test_high_entropy_value_without_keyword_is_missed(self):
        paths = {"web/fixture.py": 'blob = "aB3xK9mQ2pL7vN4rT8wZ"\n'}
        self.assertEqual(scan(paths)["entries"], [])

    def test_same_secret_in_two_repos_is_not_merged(self):
        paths = {
            "web/a.py": 'api_key = "samevalue"\n',
            "ops/b.py": 'api_key = "samevalue"\n',
        }
        entries = scan(paths)["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(report()["duplicates"], 0)

    def test_report_groups_by_repo(self):
        scan(PATHS)
        body = report()
        self.assertEqual(body["repos"], {"web": 1})
        self.assertEqual(body["open"], 1)
