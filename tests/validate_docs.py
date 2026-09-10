"""Valida enlaces, imágenes y artefactos copiables del laboratorio."""
from pathlib import Path
import json
import re

root = Path(__file__).resolve().parents[1]
readme = (root / "README.md").read_text()

for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", readme):
    if target.startswith(("http:", "https:", "#")):
        continue
    assert (root / target.split("#")[0]).exists(), target

images = re.findall(r"!\[[^\]]*\]\(assets/([^)]*)\)", readme)
prompts = json.loads((root / "assets/prompts.json").read_text())
assert len(images) == 3
assert set(images) == {item["file"] for item in prompts["images"]}
assert all((root / "assets" / image).stat().st_size > 100_000 for image in images)

preview = json.loads((root / "events/preview.json").read_text())
apply = json.loads((root / "events/apply.json").read_text())
assert preview == {"dry_run": True}
assert apply == {"dry_run": False}

lambda_policy = json.loads(
    (root / "policies/lambda-ec2-scheduler.json").read_text())
assert lambda_policy["Statement"][0]["Action"] == "ec2:DescribeInstances"
control = lambda_policy["Statement"][1]
assert set(control["Action"]) == {"ec2:StartInstances", "ec2:StopInstances"}
assert control["Condition"]["StringEquals"] == {
    "ec2:ResourceTag/Project": "m5-clase1",
    "ec2:ResourceTag/AutoSchedule": "true",
}

assert "CloudShell" in readme
assert not (root / "infra").exists()

print("README, imágenes, eventos y políticas: OK")
