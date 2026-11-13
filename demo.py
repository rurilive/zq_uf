import json

from dqrules import run

ROWS = [
    {"id": "1", "name": "甲", "age": 20, "status": "active"},
    {"id": "2", "name": "", "age": 17, "status": "active"},
    {"id": "2", "name": "丙", "age": 300, "status": "unknown"},
    {"id": "3", "name": "丁", "age": 40, "status": "inactive"},
    {"id": "4", "name": "戊", "age": "N/A", "status": "active"},
]

RULES = {
    "sample_size": 3,
    "thresholds": {"warning": 0.1, "error": 0.4},
    "rules": [
        {"name": "id_required", "type": "not_null", "field": "id"},
        {"name": "id_unique", "type": "unique", "field": "id",
         "depends_on": ["id_required"]},
        {"name": "name_required", "type": "not_null", "field": "name"},
        {"name": "age_range", "type": "range", "field": "age",
         "min": 18, "max": 150},
        {"name": "status_enum", "type": "enum", "field": "status",
         "allowed": ["active", "inactive"]},
        {"name": "adult_or_senior", "type": "expression",
         "expression": "int(row['age']) >= 18"},
    ],
}

if __name__ == "__main__":
    report = run(ROWS, RULES)
    print(json.dumps(report, ensure_ascii=False, indent=2))
