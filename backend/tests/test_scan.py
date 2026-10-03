from datetime import date
from types import SimpleNamespace

import pytest

from bezbarier.classification import FeatureType, GeoPoint
from bezbarier.detection import Detection, ImageRef
from bezbarier.detection.vlm_common import VlmDetection, VlmResult, to_attrs
from bezbarier.scan import bearing_deg, faces, scan_images

PLACE = GeoPoint(lat=50.0617, lon=19.9373)
SOUTH_OF_PLACE = GeoPoint(lat=50.06155, lon=19.9373)  # ~17 m na południe


def image(img_id: str, location: GeoPoint = SOUTH_OF_PLACE, heading: float | None = 0) -> ImageRef:
    return ImageRef(id=img_id, provider="mapillary", location=location, heading=heading, captured_at=date(2025, 7, 1))


class FakeDetector:
    model_name = "fake-vlm"

    def __init__(self, fail_on: set[str] = frozenset()):
        self.calls: list[str] = []
        self.fail_on = fail_on

    def detect(self, image, image_bytes=None):
        self.calls.append(image.id)
        if image.id in self.fail_on:
            raise RuntimeError("429 limit zapytań")
        return [
            Detection(type=FeatureType.ENTRANCE, attrs={"width_cm": {"lo": 80, "hi": 95}}, confidence=0.7),
            Detection(type=FeatureType.SURFACE, attrs={"value": "sett"}, confidence=0.9),
        ]


def test_bearing():
    assert bearing_deg(SOUTH_OF_PLACE, PLACE) == pytest.approx(0, abs=0.5)
    assert bearing_deg(PLACE, SOUTH_OF_PLACE) == pytest.approx(180, abs=0.5)


def test_faces():
    assert faces(image("a", heading=10), PLACE)
    assert not faces(image("b", heading=180), PLACE)  # kamera odwrócona
    assert not faces(image("c", heading=None), PLACE)
    assert not faces(image("d", location=GeoPoint(lat=50.0610, lon=19.9373)), PLACE)  # za daleko


def test_entrance_linked_only_when_camera_faces_place():
    result = scan_images("p", PLACE, [image("front", heading=0), image("back", heading=180)], FakeDetector(), set())
    entrances = [o for o in result.observations if o.type == FeatureType.ENTRANCE]
    assert len(entrances) == 1
    assert entrances[0].place_id == "p"
    assert sum(o.type == FeatureType.SURFACE for o in result.observations) == 2
    assert all(o.source.observed_at == date(2025, 7, 1) for o in result.observations)


def test_images_analyzed_once_and_nearest_first():
    analyzed: set[str] = set()
    far = image("far", location=GeoPoint(lat=50.0614, lon=19.9373))
    detector = FakeDetector()
    first = scan_images("p", PLACE, [far, image("near")], detector, analyzed, max_images=1)
    assert detector.calls == ["near"]
    second = scan_images("p", PLACE, [far, image("near")], detector, analyzed, max_images=2)
    assert detector.calls == ["near", "far"]
    assert (first.images_analyzed, second.images_analyzed, second.images_skipped) == (1, 1, 1)


def test_detector_error_does_not_stop_scan():
    result = scan_images("p", PLACE, [image("bad"), image("good")], FakeDetector(fail_on={"bad"}), set())
    assert result.images_analyzed == 1
    assert "429" in result.errors[0]


def _vlm(**fields) -> VlmDetection:
    base = {name: None for name in VlmDetection.model_fields}
    base.update(confidence=0.8, description="opis")
    base.update(fields)
    return VlmDetection(**base)


def test_vlm_mapping():
    attrs = to_attrs(_vlm(type=FeatureType.KERB, kerb_kind="raised", height_cm_min=10, height_cm_max=None))
    assert attrs == {"kind": "raised", "height_cm": {"lo": 10, "hi": 10}, "description": "opis"}
    attrs = to_attrs(_vlm(type=FeatureType.OBSTACLE, obstacle_kind="słup", blocks_path=False))
    assert attrs == {"kind": "słup", "blocks_path": False, "description": "opis"}


def test_gemini_detector_request_and_parsing(monkeypatch):
    pytest.importorskip("google.genai", reason='brak google-genai: pip install -e ".[gemini]"')
    from bezbarier.detection.gemini_vlm import GeminiVisionDetector

    captured = {}
    output = VlmResult(detections=[_vlm(type=FeatureType.SURFACE, surface="sett")]).model_dump_json()

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_text=output)

    client = SimpleNamespace(interactions=SimpleNamespace(create=create))
    detector = GeminiVisionDetector(client=client, model="gemini-test")
    detections = detector.detect(image("x"), image_bytes=b"\xff\xd8 fake jpeg")

    assert detections[0].attrs["value"] == "sett"
    assert captured["model"] == "gemini-test"
    assert captured["store"] is False
    assert captured["input"][0]["mime_type"] == "image/jpeg"
    assert captured["response_format"]["schema"]["properties"]["detections"]["type"] == "array"
