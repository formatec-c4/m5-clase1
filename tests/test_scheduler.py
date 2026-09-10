import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    "scheduler", Path(__file__).parents[1] / "lambda/scheduler.py")
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tags = {
            "Name": "m5-clase1-scheduled",
            "Project": "m5-clase1",
            "AutoSchedule": "true",
            "StartTime": "09:00",
            "StopTime": "18:00",
            "ActiveDays": "mon,tue,wed,thu,fri",
            "TimeZone": "UTC",
        }

    def test_day_window_boundaries(self):
        cases = [("08:59", "stopped"), ("09:00", "running"),
                 ("17:59", "running"), ("18:00", "stopped")]
        for hour, expected in cases:
            with self.subTest(hour=hour):
                state, _ = scheduler.desired_state(
                    self.tags, moment(f"2026-09-10T{hour}:00"))
                self.assertEqual(state, expected)

    def test_weekend(self):
        state, _ = scheduler.desired_state(
            self.tags, moment("2026-09-12T12:00:00"))
        self.assertEqual(state, "stopped")

    def test_local_timezone(self):
        self.tags["TimeZone"] = "America/Argentina/Mendoza"
        self.assertEqual(scheduler.desired_state(
            self.tags, moment("2026-09-10T11:59:00"))[0], "stopped")
        self.assertEqual(scheduler.desired_state(
            self.tags, moment("2026-09-10T12:00:00"))[0], "running")

    def test_overnight_uses_start_day(self):
        self.tags.update(StartTime="22:00", StopTime="06:00",
                         ActiveDays="fri")
        cases = [("2026-09-11T23:00:00", "running"),
                 ("2026-09-12T05:59:00", "running"),
                 ("2026-09-12T06:00:00", "stopped"),
                 ("2026-09-12T23:00:00", "stopped")]
        for date, expected in cases:
            with self.subTest(date=date):
                self.assertEqual(
                    scheduler.desired_state(self.tags, moment(date))[0], expected)

    def test_dst_wall_clock(self):
        self.tags.update(TimeZone="America/New_York", StartTime="01:00",
                         StopTime="03:00", ActiveDays="sun")
        for hour in ("05:30", "06:30"):
            self.assertEqual(scheduler.desired_state(
                self.tags, moment(f"2026-11-01T{hour}:00"))[0], "running")

    def test_invalid_tags_are_skipped(self):
        bad_values = [("StartTime", "25:00"), ("StopTime", "09:00"),
                      ("TimeZone", "Fake/Zone"), ("ActiveDays", "lunes"),
                      ("ActiveDays", "")]
        for key, value in bad_values:
            with self.subTest(key=key):
                tags = {**self.tags, key: value}
                self.assertIsNone(scheduler.desired_state(
                    tags, moment("2026-09-10T12:00:00"))[0])

    def test_excluded(self):
        self.assertEqual(scheduler.desired_state(
            {"AutoSchedule": "false"}, moment("2026-09-10T12:00:00")),
            (None, "excluded"))

    def client(self, state="running", excluded=False):
        client = Mock()
        tags = {**self.tags,
                "AutoSchedule": "false" if excluded else "true"}
        client.get_paginator.return_value.paginate.return_value = [{
            "Reservations": [{"Instances": [{
                "InstanceId": "i-lab",
                "State": {"Name": state},
                "Tags": [{"Key": key, "Value": value}
                         for key, value in tags.items()],
            }]}]
        }]
        return client

    def test_preview_never_mutates(self):
        client = self.client()
        result = scheduler.reconcile(
            client, moment("2026-09-10T20:00:00"), True)
        self.assertEqual(result[0]["action"], "stop")
        client.stop_instances.assert_not_called()

    def test_search_is_scoped_to_project_and_live_states(self):
        client = self.client()
        scheduler.reconcile(client, moment("2026-09-10T12:00:00"), True)
        client.get_paginator.return_value.paginate.assert_called_once_with(
            Filters=[
                {"Name": "tag:Project", "Values": ["m5-clase1"]},
                {"Name": "instance-state-name", "Values": [
                    "pending", "running", "stopping", "stopped"]},
            ])

    def test_stop_and_start(self):
        client = self.client()
        scheduler.reconcile(client, moment("2026-09-10T20:00:00"), False)
        client.stop_instances.assert_called_once_with(InstanceIds=["i-lab"])
        client = self.client("stopped")
        scheduler.reconcile(client, moment("2026-09-10T12:00:00"), False)
        client.start_instances.assert_called_once_with(InstanceIds=["i-lab"])

    def test_idempotent_and_transitions(self):
        for state in ("stopped", "stopping", "pending"):
            client = self.client(state)
            scheduler.reconcile(client, moment("2026-09-10T20:00:00"), False)
            client.start_instances.assert_not_called()
            client.stop_instances.assert_not_called()

    def test_excluded_never_mutates(self):
        client = self.client(excluded=True)
        scheduler.reconcile(client, moment("2026-09-10T20:00:00"), False)
        client.stop_instances.assert_not_called()

    def test_api_failure_is_reported(self):
        client = self.client()
        client.stop_instances.side_effect = RuntimeError("AccessDenied")
        with self.assertRaises(RuntimeError):
            scheduler.reconcile(
                client, moment("2026-09-10T20:00:00"), False)


if __name__ == "__main__":
    unittest.main()
