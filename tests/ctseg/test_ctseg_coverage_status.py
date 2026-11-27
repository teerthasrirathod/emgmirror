from emgmirror.ctseg.coverage import anatomy_coverage


def test_coverage_distinguishes_unavailable_and_does_not_infer_field_of_view():
    source = "moose:clin_ct_peripheral_bones"
    coverage = anatomy_coverage(
        [source], [{"source": source, "status": "unavailable"}], set(), set(), "research"
    )
    radius = next(item for item in coverage if item["anatomy_id"] == "radius_left")
    assert radius["status"] == "unavailable"
    assert radius["scheduled_models"] == [source]
    assert radius["field_of_view_status"] == "not_assessed"


def test_completed_model_without_label_is_not_detected_not_absent():
    source = "moose:clin_ct_peripheral_bones"
    coverage = anatomy_coverage(
        [source], [{"source": source, "status": "ok"}], set(), set(), "research"
    )
    radius = next(item for item in coverage if item["anatomy_id"] == "radius_left")
    assert radius["status"] == "not_detected"
    assert radius["field_of_view_status"] == "not_assessed"


def test_missing_selected_provider_is_not_hidden_by_another_successful_provider():
    total = "totalsegmentator:total"
    mr = "totalsegmentator:total_mr"
    coverage = anatomy_coverage(
        [total, mr],
        [{"source": total, "status": "ok"}, {"source": mr, "status": "unavailable"}],
        set(), set(), "research",
    )
    spleen = next(item for item in coverage if item["anatomy_id"] == "spleen")
    assert spleen["status"] == "unavailable"
    assert mr in spleen["unavailable_models"]
