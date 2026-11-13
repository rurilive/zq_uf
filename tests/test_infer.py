import os
import tempfile
import unittest

from metastore.infer import infer_fields, infer_from_csv


class InferFieldsTest(unittest.TestCase):
    def test_infer_types(self):
        rows = [
            {"id": "1", "amount": "10.5", "flag": "true", "note": "x"},
            {"id": "2", "amount": "3", "flag": "false", "note": "y"},
        ]
        fields = {field["name"]: field for field in infer_fields(rows)}
        self.assertEqual(fields["id"]["type"], "int")
        self.assertEqual(fields["amount"]["type"], "float")
        self.assertEqual(fields["flag"]["type"], "bool")
        self.assertEqual(fields["note"]["type"], "str")

    def test_null_rate(self):
        rows = [{"a": "1", "b": ""}, {"a": "null", "b": "2"}, {"a": "3", "b": None}, {"a": "4", "b": "5"}]
        fields = {field["name"]: field for field in infer_fields(rows)}
        self.assertEqual(fields["a"]["null_rate"], 0.25)
        self.assertEqual(fields["b"]["null_rate"], 0.5)
        self.assertEqual(fields["a"]["type"], "int")

    def test_unknown_type_when_all_null(self):
        fields = infer_fields([{"a": ""}, {"a": None}])
        self.assertEqual(fields[0]["type"], "unknown")
        self.assertEqual(fields[0]["null_rate"], 1.0)

    def test_infer_from_csv(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as handle:
            handle.write("id,amount\n1,10.5\n2,\n")
            path = handle.name
        try:
            fields = {field["name"]: field for field in infer_from_csv(path)}
        finally:
            os.unlink(path)
        self.assertEqual(fields["id"]["type"], "int")
        self.assertEqual(fields["amount"]["type"], "float")
        self.assertEqual(fields["amount"]["null_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
