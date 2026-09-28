from datetime import datetime

import pytest
import requests

START = datetime(2026, 1, 1, 10, 0, 0)
LATER = datetime(2026, 1, 1, 10, 5, 0)


def minimal_fit(**extra):
    return {"record": [{"timestamp": START, "heart_rate": 120}], **extra}


def points_for(points, measurement, activity_id=None):
    return [
        p for p in points
        if p["measurement"] == measurement and (activity_id is None or p["tags"]["ActivityID"] == activity_id)
    ]


def serve(gf, fit_zips, tcx_result=RuntimeError("no TCX either")):
    """Serves FIT zips by activity ID; missing IDs fail the FIT download and fall back to TCX.

    tcx_result is raised if it is an exception, otherwise returned as the TCX download.
    """
    tcx = gf.garmin_obj.ActivityDownloadFormat.TCX

    def download_activity(activity_id, dl_fmt=None):
        if dl_fmt is tcx:
            if isinstance(tcx_result, Exception):
                raise tcx_result
            return tcx_result
        if activity_id not in fit_zips:
            raise FileNotFoundError(f"no FIT file for {activity_id}")
        return fit_zips[activity_id]

    gf.garmin_obj.download_activity.side_effect = download_activity


def downloaded_ids(gf):
    return [c.args[0] for c in gf.garmin_obj.download_activity.call_args_list]


@pytest.mark.parametrize("order", [(1, 2, 3), (2, 1, 3), (2, 3, 1)], ids=["parsed-first", "parsed-middle", "parsed-last"])
def test_already_parsed_activity_is_skipped_without_dropping_others(gf, fake_fit, monkeypatch, order):
    monkeypatch.setattr(gf, "FORCE_REPROCESS_ACTIVITIES", False)
    gf.PARSED_ACTIVITY_ID_LIST.append(1)
    serve(gf, {2: fake_fit("a2", minimal_fit()), 3: fake_fit("a3", minimal_fit())})

    points = gf.fetch_activity_GPS({activity_id: "running" for activity_id in order})

    assert 1 not in downloaded_ids(gf)
    assert points_for(points, "ActivityGPS", 2) and points_for(points, "ActivityGPS", 3)


def test_already_parsed_activity_is_reprocessed_when_forced(gf, fake_fit, monkeypatch):
    monkeypatch.setattr(gf, "FORCE_REPROCESS_ACTIVITIES", True)
    gf.PARSED_ACTIVITY_ID_LIST.append(1)
    serve(gf, {1: fake_fit("a1", minimal_fit())})

    points = gf.fetch_activity_GPS({1: "running"})

    assert downloaded_ids(gf) == [1]
    assert points_for(points, "ActivityGPS", 1)


def test_parsed_activities_are_recorded(gf, fake_fit):
    serve(gf, {1: fake_fit("a1", minimal_fit())})  # activity 2 fails both FIT and TCX

    gf.fetch_activity_GPS({1: "running", 2: "running"})

    assert gf.PARSED_ACTIVITY_ID_LIST == [1]


@pytest.mark.parametrize("tcx_result", [
    requests.exceptions.Timeout("read timed out"),
    RuntimeError("no TCX either"),
    b"",  # what the bulk importer's GarminBulkExport returns for TCX downloads
], ids=["timeout", "error", "empty-tcx"])
@pytest.mark.parametrize("order", [(1, 2), (2, 1)], ids=["failure-first", "failure-last"])
def test_tcx_fallback_failure_keeps_other_activities(gf, fake_fit, order, tcx_result):
    serve(gf, {1: fake_fit("ok", minimal_fit())}, tcx_result=tcx_result)  # activity 2 fails FIT and TCX

    points = gf.fetch_activity_GPS({activity_id: "running" for activity_id in order})

    assert points_for(points, "ActivityGPS", 1)
    assert not points_for(points, "ActivityGPS", 2)
    assert downloaded_ids(gf).count(2) == 2  # FIT attempt, then TCX attempt


def test_session_lengths_and_laps_are_not_swapped(gf, fake_fit):
    session = {"start_time": START, "num_laps": 3, "num_lengths": 40, "message_index": 0}
    serve(gf, {1: fake_fit("swim", minimal_fit(session=[session]))})

    fields = points_for(gf.fetch_activity_GPS({1: "lap_swimming"}), "ActivitySession")[0]["fields"]

    assert fields["Laps"] == 3
    assert fields["Lengths"] == 40


MESSAGE_TYPES = pytest.mark.parametrize("message, measurement", [
    ("session", "ActivitySession"),
    ("length", "ActivityLength"),
    ("lap", "ActivityLap"),
])


@MESSAGE_TYPES
@pytest.mark.parametrize("start_time", [None, "missing"])
def test_time_falls_back_to_timestamp_without_start_time(gf, fake_fit, message, measurement, start_time):
    record = {"timestamp": LATER, "message_index": 0}
    if start_time != "missing":
        record["start_time"] = start_time
    serve(gf, {1: fake_fit("fit", minimal_fit(**{message: [record]}))})

    point = points_for(gf.fetch_activity_GPS({1: "running"}), measurement)[0]

    assert point["time"] == "2026-01-01T10:05:00+00:00"


@MESSAGE_TYPES
def test_start_time_is_preferred_over_timestamp(gf, fake_fit, message, measurement):
    record = {"start_time": START, "timestamp": LATER, "message_index": 0}
    serve(gf, {1: fake_fit("fit", minimal_fit(**{message: [record]}))})

    point = points_for(gf.fetch_activity_GPS({1: "running"}), measurement)[0]

    assert point["time"] == "2026-01-01T10:00:00+00:00"


@MESSAGE_TYPES
def test_record_without_start_time_or_timestamp_is_skipped(gf, fake_fit, message, measurement):
    record = {"start_time": None, "timestamp": None, "message_index": 0}
    serve(gf, {1: fake_fit("fit", minimal_fit(**{message: [record]}))})

    points = gf.fetch_activity_GPS({1: "running"})

    assert not points_for(points, measurement)
    assert points_for(points, "ActivityGPS")


@MESSAGE_TYPES
@pytest.mark.parametrize("message_index, expected_index", [(0, 1), (2, 3), (None, 0), ("missing", 0)])
def test_message_index_is_converted_safely(gf, fake_fit, message, measurement, message_index, expected_index):
    record = {"start_time": START}
    if message_index != "missing":
        record["message_index"] = message_index
    serve(gf, {1: fake_fit("fit", minimal_fit(**{message: [record]}))})

    point = points_for(gf.fetch_activity_GPS({1: "running"}), measurement)[0]

    assert point["fields"]["Index"] == expected_index
