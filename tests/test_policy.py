import unittest

from retryall import ErrorClass, HttpError, Policy, call


class PolicyTest(unittest.TestCase):
    def test_defaults_preserved_when_empty(self):
        policy = Policy()
        self.assertEqual(policy.base_delay, 0.01)
        self.assertEqual(policy.factor, 2.0)
        self.assertEqual(policy.jitter, 0.25)
        self.assertIsNone(policy.attempts)
        self.assertEqual(
            policy.class_config(ErrorClass.RATE_LIMIT)["attempts"], 5
        )

    def test_class_level_attempt_override(self):
        calls = {"n": 0}

        def timeout():
            calls["n"] += 1
            raise TimeoutError("slow")

        with self.assertRaises(TimeoutError):
            call(
                timeout,
                {
                    "base_delay": 0,
                    "classes": {
                        ErrorClass.TIMEOUT: {"attempts": 2},
                    },
                },
            )
        self.assertEqual(calls["n"], 2)

    def test_site_override_attempts_and_max_delay(self):
        calls = {"n": 0}
        policy = Policy(
            {
                "base_delay": 0,
                "sites": {
                    "slow-site": {
                        "classes": {
                            ErrorClass.SERVER: {
                                "attempts": 2,
                                "max_delay": 0.25,
                            },
                        },
                    },
                },
            }
        )

        def boom():
            calls["n"] += 1
            raise HttpError(503)

        with self.assertRaises(HttpError):
            call(boom, policy.for_site("slow-site"))
        self.assertEqual(calls["n"], 2)
        active = policy.for_site("slow-site")
        self.assertEqual(
            active.class_config(ErrorClass.SERVER)["max_delay"], 0.25
        )

    def test_site_override_via_dict_policy(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise TimeoutError("slow")

        with self.assertRaises(TimeoutError):
            call(
                boom,
                {
                    "base_delay": 0,
                    "site": "edge",
                    "sites": {
                        "edge": {
                            "classes": {
                                ErrorClass.TIMEOUT: {"attempts": 1},
                            },
                        },
                    },
                },
            )
        self.assertEqual(calls["n"], 1)

    def test_sites_without_match_use_global_policy(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise TimeoutError("slow")

        with self.assertRaises(TimeoutError):
            call(boom, {"base_delay": 0, "site": "other"})
        self.assertEqual(calls["n"], 3)

    def test_policy_is_reusable_and_not_mutated(self):
        policy = Policy({"base_delay": 0.5})
        policy.for_site("edge")
        self.assertEqual(policy.base_delay, 0.5)
        self.assertIsNone(policy.site)
