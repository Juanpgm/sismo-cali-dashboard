"""Self-check for _photo_urls_from_groups. Run: python scripts/test_photo_urls.py

Verifies the pure attachmentGroups -> {objectid: [url]} logic without the
network: only image attachments, signatures (firma*) excluded, correct URL shape.
"""
from refresh_data import _photo_urls_from_groups, SURVEY_LAYER_URL

groups = [
    {
        "parentObjectId": 100,
        "attachmentInfos": [
            {"id": 5, "name": "foto_1.jpg", "contentType": "image/jpeg"},
            {"id": 9, "name": "firma-eval.png", "contentType": "image/png"},   # signature -> skip
            {"id": 3, "name": "acta.pdf", "contentType": "application/pdf"},    # not image -> skip
            {"id": 7, "name": "foto_2.jpg", "contentType": "image/jpeg"},
        ],
    },
    {"parentObjectId": 200, "attachmentInfos": [
        {"id": 1, "name": "firma.png", "contentType": "image/png"},            # only a signature
    ]},
    {"parentObjectId": None, "attachmentInfos": [                              # no parent -> skip
        {"id": 2, "name": "foto.jpg", "contentType": "image/jpeg"},
    ]},
]

out = _photo_urls_from_groups(groups)

assert out == {100: [
    f"{SURVEY_LAYER_URL}/100/attachments/5",
    f"{SURVEY_LAYER_URL}/100/attachments/7",
]}, out
# 200 has only a signature -> no entry; None parent -> skipped.
assert 200 not in out and None not in out, out
# Empty input -> empty map.
assert _photo_urls_from_groups([]) == {}

print("scripts/test_photo_urls.py OK")
