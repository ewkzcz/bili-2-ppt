import importlib.util
import json
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assign_frames.py"


def load_module():
    spec = importlib.util.spec_from_file_location("assign_frames", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_assigns_by_overlap_and_flags_boundary_frames(tmp_path):
    module = load_module()
    files = {}
    for name in ("a", "b", "c", "d"):
        files[name] = tmp_path / f"{name}.png"
        Image.new("RGB", (64, 36), (40, 40, 40)).save(files[name])
    keep = {"parts": [
        {"part": 1, "kept": [
            {"time": 50.0, "run_start": 10.0, "file": str(files["a"])},   # 全在 s1
            {"time": 130.0, "run_start": 70.0, "file": str(files["b"])},  # s1 30 秒、s2 30 秒 -> 待复核
            {"time": 200.0, "file": str(files["c"])},                      # 无 run_start，单点落在 s2
        ]},
        {"part": 2, "kept": [{"time": 20.0, "run_start": 0.0, "file": str(files["d"])}]},  # 不在任何时间段
    ]}
    plan = {"theme": "t", "sections": [
        {"id": "s1", "title": "一", "spans": [{"part": 1, "start": 0, "end": 100}]},
        {"id": "s2", "title": "二", "spans": [{"part": 1, "start": 100, "end": 300}]},
    ]}
    keep_path, plan_path = tmp_path / "dedup_keep.json", tmp_path / "notes.plan.json"
    keep_path.write_text(json.dumps(keep), encoding="utf-8")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert module.main(["--plan", str(plan_path), "--keep", str(keep_path)]) == 0

    result = json.loads(plan_path.read_text(encoding="utf-8"))
    s1, s2 = result["sections"]
    s1_files, s2_files = [f["file"] for f in s1["frames"]], [f["file"] for f in s2["frames"]]
    assert str(files["a"]) in s1_files and str(files["c"]) in s2_files
    assert (str(files["b"]) in s1_files) != (str(files["b"]) in s2_files)
    assert str(files["d"]) not in s1_files + s2_files
    report = json.loads((tmp_path / "frame_assign.json").read_text(encoding="utf-8"))
    reasons = {Path(r["file"]).stem: r["reason"] for r in report["review"]}
    assert reasons == {"b": "spans-two-sections", "d": "outside-all-spans"}
    assert (tmp_path / "frame_sheets" / "s1.jpg").exists()
    assert (tmp_path / "frame_sheets" / "_outside.jpg").exists()
