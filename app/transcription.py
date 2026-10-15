"""Optional local speech recognition, used only when explicitly configured."""

import tempfile
from pathlib import Path


def transcribe(video_id, model_name):
    import yt_dlp
    from faster_whisper import WhisperModel

    with tempfile.TemporaryDirectory(prefix="gamepulse-") as work:

        def duration_limit(info, *, incomplete):
            if info.get("duration", 0) > 10800:
                return "Video exceeds the 3-hour transcription limit"
            return None

        options = {
            "format": "worstaudio/worst",
            "outtmpl": str(Path(work) / "audio.%(ext)s"),
            "noplaylist": True,
            "socket_timeout": 30,
            "retries": 1,
            "quiet": True,
            "max_filesize": 300 * 1024 * 1024,
            "match_filter": duration_limit,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            if not info:
                raise ValueError("No audio downloaded")
            path = downloader.prepare_filename(info)
        if not Path(path).is_file():
            raise ValueError("Audio unavailable or exceeds size limit")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(path, vad_filter=True)
        return [{"text": segment.text, "start": segment.start} for segment in segments]


def transcript_chunks(segments):
    # Preserve continuous text in chunks rather than cherry-picking individual subtitles.
    chunks, text, start = [], "", 0
    for segment in segments:
        body = segment["text"].strip()
        if text and len(text) + len(body) > 2400:
            chunks.append({"id": f"transcript-{len(chunks)}", "text": text, "start": start})
            text = ""
        if not text:
            start = segment["start"]
        text = (text + " " + body).strip()
    if text:
        chunks.append({"id": f"transcript-{len(chunks)}", "text": text, "start": start})
    if len(chunks) > 40:
        return [chunks[round(i * (len(chunks) - 1) / 39)] for i in range(40)]
    return chunks
