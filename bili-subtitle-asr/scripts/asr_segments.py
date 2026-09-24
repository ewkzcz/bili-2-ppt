#!/usr/bin/env python3
"""将本地音频分段转写并恢复整集时间轴，所有适配器均来自同目录。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import extract_bilibili as extractor


def merge_chunks(chunks: list[dict], duration: float) -> dict:
    """合并局部时间戳；没有句级时间戳的结果明确标记为整段估计。"""
    segments = []
    backends = set()
    for chunk in sorted(chunks, key=lambda item: item['offset']):
        offset, length, data = chunk['offset'], chunk['duration'], chunk['result']
        backends.add(data.get('backend', 'unknown'))
        source_segments = data.get('segments') or []
        if not source_segments and str(data.get('text', '')).strip():
            source_segments = [{'start': 0, 'end': length, 'text': data['text'], 'timing': 'chunk-estimate'}]
        for segment in source_segments:
            start, end = float(segment['start']), float(segment['end'])
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > length + 0.5:
                raise ValueError('ASR 分段时间戳无效或超出音频范围')
            text = str(segment.get('text', '')).strip()
            if not text:
                continue
            timing = segment.get('timing', 'chunk-estimate' if data.get('backend') == 'qwen3-asr' else 'segment')
            segments.append({'id': len(segments) + 1, 'start': offset + start,
                             'end': min(offset + end, duration), 'text': text, 'timing': timing})
    return {'source': 'audio-asr', 'backend': ','.join(sorted(backends)), 'duration': duration,
            'text': '\n'.join(s['text'] for s in segments), 'segments': segments,
            'processed_audio_seconds': sum(c['duration'] for c in chunks)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--segment-seconds', type=float, default=180)
    parser.add_argument('--backend', choices=['auto', 'faster-whisper', 'openai-whisper', 'qwen3-asr', 'funasr'], default='auto')
    parser.add_argument('--model')
    parser.add_argument('--language', default='zh')
    args = parser.parse_args()
    if not math.isfinite(args.segment_seconds) or args.segment_seconds <= 0:
        parser.error('--segment-seconds 必须为正数')
    args.audio = args.audio.resolve(strict=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    backend = extractor.resolve_asr_backend(args.backend, args.language)
    model = args.model or {'qwen3-asr': 'Qwen/Qwen3-ASR-0.6B', 'funasr': 'paraformer-zh'}.get(backend, 'small')
    # 输入和模型配置共同决定缓存身份，修改音频或模型后不会误用旧转写。
    digest = hashlib.sha256()
    with args.audio.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    config = {'audio_sha256': digest.hexdigest(), 'backend': backend, 'model': model,
              'language': args.language, 'segment_seconds': args.segment_seconds}
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    run = args.work_dir / key
    run.mkdir(exist_ok=True)
    # 独占锁只覆盖本次运行；系统退出时自动释放，不留永久占用标记。
    lock = (run / 'run.lock').open('a')
    if sys.platform != 'win32':
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        import msvcrt
        lock.seek(0); lock.write('0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    normalized = run / 'input.wav'
    if not normalized.exists():
        temporary = run / 'input.pending.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(args.audio), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(temporary)], check=True)
        temporary.replace(normalized)
    chunks = []
    with wave.open(str(normalized), 'rb') as source:
        rate = source.getframerate()
        total = source.getnframes()
        step = max(1, round(args.segment_seconds * rate))
        duration = total / rate
        for index, frame in enumerate(range(0, total, step)):
            offset, length = frame / rate, min(step, total - frame) / rate
            folder = run / f'seg_{index:04d}'
            folder.mkdir(exist_ok=True)
            wav = folder / 'audio.wav'
            source.setpos(frame)
            raw = source.readframes(step)
            result_path = folder / 'result.json'
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding='utf-8'))
            else:
                with wave.open(str(wav), 'wb') as target:
                    target.setparams(source.getparams()); target.writeframes(raw)
                manifest = [{'page': 1, 'cid': index, 'wav': str(wav)}]
                outputs = extractor.transcribe_wavs(manifest, folder, backend, model, [], force=True, language=args.language)
                result = json.loads(Path(outputs[0]['transcript_json']).read_text(encoding='utf-8'))
                merge_chunks([{'offset': offset, 'duration': length, 'result': result}], duration)
                pending = folder / 'result.pending.json'
                pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                pending.replace(result_path)
            chunks.append({'offset': offset, 'duration': length, 'result': result})
    payload = merge_chunks(chunks, duration)
    payload['config'] = config
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    args.out.with_suffix('.txt').write_text(payload['text'] + '\n', encoding='utf-8')
    print(args.out)
    lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
