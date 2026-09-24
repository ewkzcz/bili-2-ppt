import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dedupe_frames.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dedupe_frames", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_frame(path: Path, gray: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (gray, gray, gray)).save(path)
    return path


def make_log(tmp_path: Path, grays: list[int], part: int = 1, step: float = 10.0) -> tuple[Path, list[dict]]:
    """按灰度序列造帧和 capture_log.jsonl，返回日志路径和帧记录。"""
    records = []
    for index, gray in enumerate(grays):
        path = write_frame(tmp_path / "frames" / f"{index:05d}.png", gray)
        records.append({"part": part, "actual_time": index * step, "file": str(path)})
    log = tmp_path / "capture_log.jsonl"
    log.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return log, records


def build_entries(module, source: Path, records: list[dict]):
    return [module.build_entry(record, source, index, None) for index, record in enumerate(records, start=1)]


def test_duplicate_runs_keep_the_last_frame(tmp_path):
    module = load_module()
    log, records = make_log(tmp_path, [100, 100, 100, 200, 200, 20])
    kept, dropped = module.dedupe_part(build_entries(module, log, records), 9.0)

    assert [Path(entry["file"]).name for entry in kept] == ["00002.png", "00004.png", "00005.png"]
    assert len(dropped) == 3
    assert Path(dropped[0]["folded_into"]).name == "00002.png"
    assert dropped[0]["reason"] == "visual-duplicate"


def test_near_duplicate_frames_collapse(tmp_path):
    module = load_module()
    log, records = make_log(tmp_path, [100, 103, 106, 200])
    kept, dropped = module.dedupe_part(build_entries(module, log, records), 9.0)

    assert [Path(entry["file"]).name for entry in kept] == ["00002.png", "00003.png"]
    assert len(dropped) == 2


def test_distinct_frames_are_all_kept(tmp_path):
    module = load_module()
    log, records = make_log(tmp_path, [0, 120, 240])
    kept, dropped = module.dedupe_part(build_entries(module, log, records), 9.0)

    assert len(kept) == 3
    assert dropped == []


def test_slow_drift_breaks_runs_instead_of_collapsing(tmp_path):
    """相邻差始终低于阈值时，累积差异仍要切开，否则缓慢平移只剩一帧。"""
    module = load_module()
    log, records = make_log(tmp_path, list(range(0, 44, 4)))
    entries = build_entries(module, log, records)

    runs = module.group_runs(entries, 9.0)
    kept, _ = module.dedupe_part(entries, 9.0)

    assert len(runs) > 1
    assert len(kept) == len(runs)


def test_resample_hints_flag_wide_gaps(tmp_path):
    module = load_module()
    log, records = make_log(tmp_path, [0, 200], step=200.0)
    kept, _ = module.dedupe_part(build_entries(module, log, records), 9.0)

    hints = module.collect_hints(kept, max_gap=90.0)

    assert len(hints) == 1
    assert hints[0]["gap"] == 200.0
    assert hints[0]["suggest_time"] == 100.0


def test_time_falls_back_to_filename(tmp_path):
    module = load_module()
    path = write_frame(tmp_path / "frames" / "00707.png", 10)
    entry = module.build_entry({"file": str(path)}, tmp_path, 1, None)
    assert entry["time"] == 707.0


def test_part_inferred_from_directory_name(tmp_path):
    module = load_module()
    path = write_frame(tmp_path / "P05" / "00120.png", 10)
    entry = module.build_entry({"file": str(path)}, tmp_path, 1, None)
    assert entry["part"] == 5


def test_iter_records_reads_episode_frames():
    module = load_module()
    payload = {"episodes": [{"part": 3, "duration": 900, "frames": [{"actual_time": 1, "file": "a.png"}]}]}

    records = list(module.iter_records(payload))

    assert len(records) == 1
    assert records[0]["part"] == 3
    assert records[0]["file"] == "a.png"
    assert records[0]["actual_time"] == 1


def test_load_frame_records_scans_directory(tmp_path):
    module = load_module()
    write_frame(tmp_path / "raw" / "00001.png", 10)
    write_frame(tmp_path / "raw" / "00002.png", 10)

    records = module.load_frame_records(tmp_path / "raw")

    assert len(records) == 2


def test_cli_writes_keep_log_and_hints(tmp_path):
    log, _ = make_log(tmp_path, [100, 100, 200, 200, 30], step=120.0)
    output_dir = tmp_path / "out"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(log), "--output-dir", str(output_dir), "--max-gap", "90"],
        check=True,
        text=True,
        capture_output=True,
    )

    keep = json.loads((output_dir / "dedup_keep.json").read_text(encoding="utf-8"))
    log_lines = [json.loads(line) for line in (output_dir / "dedup_log.jsonl").read_text(encoding="utf-8").splitlines()]
    hints = json.loads((output_dir / "resample_hints.json").read_text(encoding="utf-8"))

    assert keep["kept_total"] == 3
    assert keep["dropped_total"] == 2
    assert keep["keep_policy"] == "last-frame-of-each-run"
    assert [Path(entry["file"]).name for entry in keep["parts"][0]["kept"]] == ["00001.png", "00003.png", "00004.png"]
    assert len(log_lines) == 2
    assert hints["max_gap"] == 90.0
    assert [hint["suggest_time"] for hint in hints["hints"]] == [240.0, 420.0]
    assert "保留 3 丢弃 2" in result.stdout


def _main() -> int:
    """环境里常没有 pytest，保留一个标准库入口：python test_dedupe_frames.py。"""
    import inspect
    import tempfile
    import traceback

    passed = failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        try:
            if "tmp_path" in inspect.signature(function).parameters:
                with tempfile.TemporaryDirectory() as directory:
                    function(Path(directory))
            else:
                function()
            passed += 1
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
