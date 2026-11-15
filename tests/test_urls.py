import unittest

from seedscore import URLError, normalise_url, site_of, target_key, try_normalise


class NormaliseTest(unittest.TestCase):
    def test_scheme_host_case_and_trailing_slash(self):
        self.assertEqual(normalise_url("HTTPS://Example.COM/Path/"), "https://example.com/Path")

    def test_www_is_stripped(self):
        self.assertEqual(normalise_url("https://www.example.com/"), "https://example.com/")

    def test_default_port_removed(self):
        self.assertEqual(normalise_url("https://example.com:443/a"), "https://example.com/a")
        self.assertEqual(normalise_url("http://example.com:80/a"), "http://example.com/a")
        self.assertEqual(normalise_url("http://example.com:8080/a"), "http://example.com:8080/a")

    def test_fragment_removed(self):
        self.assertEqual(normalise_url("https://example.com/a#section"), "https://example.com/a")

    def test_tracking_params_removed(self):
        self.assertEqual(
            normalise_url("https://example.com/a?utm_source=x&utm_medium=y&id=3"),
            "https://example.com/a?id=3",
        )
        self.assertEqual(normalise_url("https://example.com/a?gclid=abc"), "https://example.com/a")

    def test_query_ordering_is_stable(self):
        self.assertEqual(
            normalise_url("https://example.com/a?b=2&a=1"),
            normalise_url("https://example.com/a?a=1&b=2"),
        )

    def test_session_params_removed(self):
        self.assertEqual(
            normalise_url("https://example.com/a?PHPSESSID=xyz&id=1"),
            "https://example.com/a?id=1",
        )

    def test_index_file_removed(self):
        self.assertEqual(normalise_url("https://example.com/index.html"), "https://example.com/")
        self.assertEqual(normalise_url("https://example.com/dir/index.php"), "https://example.com/dir")

    def test_duplicate_slashes_and_dot_segments(self):
        self.assertEqual(normalise_url("https://example.com//a//b/../b/"), "https://example.com/a/b")

    def test_percent_escape_normalisation(self):
        self.assertEqual(normalise_url("https://example.com/%7Euser"), "https://example.com/~user")
        self.assertEqual(normalise_url("https://example.com/a%2fb"), "https://example.com/a%2Fb")

    def test_bare_host_gets_scheme(self):
        self.assertEqual(normalise_url("example.com/a"), "http://example.com/a")

    def test_invalid_input_raises(self):
        for value in (
            "",
            "   ",
            "javascript:alert(1)",
            "ftp://example.com/",
            "http://",
            "123",
            "http://nodots/",
        ):
            with self.subTest(value=value), self.assertRaises(URLError):
                normalise_url(value)

    def test_localhost_allowed(self):
        self.assertEqual(normalise_url("http://localhost:8000/a"), "http://localhost:8000/a")

    def test_try_normalise_never_raises(self):
        self.assertEqual(try_normalise("not a url"), ("", "missing url scheme"))
        url, error = try_normalise("https://example.com/")
        self.assertIsNone(error)
        self.assertEqual(url, "https://example.com/")

    def test_target_key_ignores_scheme_and_www(self):
        self.assertEqual(
            target_key("http://www.example.com/a?utm_source=x"),
            target_key("https://example.com/a"),
        )

    def test_target_key_keeps_meaningful_query(self):
        self.assertNotEqual(
            target_key("https://example.com/a?id=1"),
            target_key("https://example.com/a?id=2"),
        )

    def test_site_of_handles_multi_label_suffix(self):
        self.assertEqual(site_of("news.example.co.uk"), "example.co.uk")
        self.assertEqual(site_of("a.b.example.com"), "example.com")
        self.assertEqual(site_of("localhost"), "localhost")


if __name__ == "__main__":
    unittest.main()
