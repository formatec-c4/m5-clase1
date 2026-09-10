"""Enciende o detiene las EC2 del laboratorio según sus tags de horario."""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT = "m5-clase1"
DAYS = {name: index for index, name in enumerate(
        ("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}


def desired_state(tags, now):
    """Devuelve running, stopped o None si la instancia no debe administrarse."""
    if tags.get("AutoSchedule") != "true":
        return None, "excluded"

    try:
        start = tags["StartTime"]
        stop = tags["StopTime"]
        if not all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value)
                   for value in (start, stop)):
            raise ValueError("horario invalido")
        if start == stop:
            raise ValueError("inicio y fin iguales")

        names = tags["ActiveDays"].split(",")
        days = {DAYS[name] for name in names}
        local = now.astimezone(ZoneInfo(tags["TimeZone"]))
        minute = local.strftime("%H:%M")

        if start < stop:
            active = local.weekday() in days and start <= minute < stop
        else:
            active = (
                local.weekday() in days and minute >= start
            ) or (
                (local - timedelta(days=1)).weekday() in days and minute < stop
            )
        return ("running" if active else "stopped"), "schedule"
    except (KeyError, ValueError, ZoneInfoNotFoundError) as error:
        return None, f"invalid_tags: {error}"


def reconcile(ec2, now, dry_run):
    """Busca únicamente el proyecto del lab y reconcilia sus instancias."""
    results = []
    failures = []
    pages = ec2.get_paginator("describe_instances").paginate(Filters=[
        {"Name": "tag:Project", "Values": [PROJECT]},
        {"Name": "instance-state-name",
         "Values": ["pending", "running", "stopping", "stopped"]},
    ])

    for page in pages:
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                tags = {tag["Key"]: tag["Value"]
                        for tag in instance.get("Tags", [])}
                desired, reason = desired_state(tags, now)
                state = instance["State"]["Name"]
                result = {
                    "instance_id": instance["InstanceId"],
                    "name": tags.get("Name", "sin-Name"),
                    "state": state,
                    "desired": desired,
                    "reason": reason,
                    "dry_run": dry_run,
                    "action": "none",
                }

                if desired and state in ("running", "stopped") and state != desired:
                    action = "start" if desired == "running" else "stop"
                    result["action"] = action
                    if not dry_run:
                        try:
                            getattr(ec2, f"{action}_instances")(
                                InstanceIds=[instance["InstanceId"]])
                        except Exception as error:
                            result["error"] = str(error)
                            failures.append(instance["InstanceId"])
                elif state in ("pending", "stopping"):
                    result["reason"] = "transition_in_progress"

                print(json.dumps(result, ensure_ascii=False))
                results.append(result)

    if failures:
        raise RuntimeError(f"EC2 actions failed: {failures}")
    return results


def lambda_handler(event, context):
    """La ausencia de dry_run produce una vista previa segura."""
    import boto3

    dry_run = event.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run debe ser true o false, sin comillas")

    return {
        "results": reconcile(
            boto3.client("ec2"), datetime.now(timezone.utc), dry_run
        )
    }
