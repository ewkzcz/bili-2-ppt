#!/usr/bin/env python3
"""仅检查本技能运行所需的解释器、工具和可选模型库，不访问网络。"""
import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='以 JSON 输出依赖状态')
    parser.parse_args()
    scripts = Path(__file__).resolve().parent
    result = {'python': sys.executable, 'ffmpeg': shutil.which('ffmpeg'),
              'ffprobe': shutil.which('ffprobe'),
              'modules': {name: importlib.util.find_spec(name) is not None for name in
                          ['yt_dlp', 'qwen_asr', 'faster_whisper', 'whisper', 'funasr']},
              'local_scripts': {name: (scripts / name).is_file() for name in
                                ['extract_bilibili.py', 'extract_bilibili_opus.py', 'run_bili_note.py',
                                 'archive_bili_materials.py', 'fetch_browser_ai_subtitles.py',
                                 'run_qwen_asr.py', 'asr_segments.py']}}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
