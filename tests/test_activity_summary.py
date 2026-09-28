import pytest


@pytest.mark.parametrize("durations, expected_end", [
    ({"elapsedDuration": 30.0, "duration": 60.0}, "2026-01-01T10:00:30+00:00"),
    ({"elapsedDuration": 30.9, "duration": 60.0}, "2026-01-01T10:00:30+00:00"),  # truncated to whole seconds
    ({"elapsedDuration": None, "duration": 60.0}, "2026-01-01T10:01:00+00:00"),
    ({"elapsedDuration": 0, "duration": 60.0}, "2026-01-01T10:01:00+00:00"),  # same fallback as the elapsedDuration field
    ({"duration": 60.0}, "2026-01-01T10:01:00+00:00"),
    ({"elapsedDuration": None, "duration": None}, "2026-01-01T10:00:00+00:00"),
    ({}, "2026-01-01T10:00:00+00:00"),
], ids=["elapsed", "elapsed-fractional", "elapsed-none", "elapsed-zero", "elapsed-missing", "both-none", "both-missing"])
def test_activity_end_point_handles_missing_durations(gf, durations, expected_end):
    gf.garmin_obj.get_activities_by_date.return_value = [{
        "activityId": 9,
        "startTimeGMT": "2026-01-01 10:00:00",
        "activityType": {"typeKey": "running"},
        **durations,
    }]
    gf.garmin_obj.get_activity_hr_in_timezones.return_value = []

    points, _, _ = gf.get_activity_summary("2026-01-01")

    start_point, end_point = points
    assert start_point["time"] == "2026-01-01T10:00:00+00:00"
    assert end_point["fields"]["activityName"] == "END"
    assert end_point["time"] == expected_end
