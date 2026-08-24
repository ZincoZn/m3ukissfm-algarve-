#!/usr/bin/env python3
import argparse, json, os, re, sys, urllib.request, html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.dom import minidom

SERVER = "https://nl.digitalrm.pt:8024"
STREAM = SERVER + "/stream"
STATUS = SERVER + "/status-json.xsl"
LOGO = "https://kissfm.pt/player/player_logo.png"
CHANNEL_ID = "kissfm.algarve"
CHANNEL_NAME = "KISS FM Algarve"
STATE_FILE = Path("data/history.json")
M3U_FILE = Path("kissfm.m3u")
XML_FILE = Path("kissfm.xml")
HEADERS = {"User-Agent": "KISSFM-Kodi-EPG/2.0"}

def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def parse_title(raw):
    raw = (raw or "").strip()
    artist = ""
    song = raw
    if " - " in raw:
        artist, song = raw.split(" - ", 1)
        artist, song = artist.strip(), song.strip()
    return artist, song

def get_info():
    req = urllib.request.Request(STATUS, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    src = data["icestats"]["source"]
    if isinstance(src, list):
        if not src:
            raise RuntimeError("Icecast não devolveu nenhum stream.")
        src = src[0]

    raw = (src.get("title") or src.get("yp_currently_playing") or "").strip()
    artist = (src.get("artist") or "").strip()
    song = raw
    if not artist:
        artist, song = parse_title(raw)

    try:
        bitrate = int(src.get("bitrate") or src.get("ice-bitrate") or 0)
    except (TypeError, ValueError):
        bitrate = 0

    return {
        "name": CHANNEL_NAME,
        "id": CHANNEL_ID,
        "stream": STREAM,
        "artist": artist,
        "song": song,
        "title": raw,
        "bitrate": bitrate,
        "codec": src.get("server_type", "audio/mpeg"),
        "sample_rate": src.get("ice-samplerate", ""),
        "channels": src.get("ice-channels", ""),
        "listeners": src.get("listeners", 0),
        "checked_at": iso(utcnow()),
    }

def load_history():
    if not STATE_FILE.exists():
        return []
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_history(history):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

def update_history(info):
    now = utcnow()
    history = load_history()
    current_key = f"{info['artist']}|{info['song']}".strip("|")

    if history:
        last = history[-1]
        last_key = f"{last.get('artist','')}|{last.get('song','')}".strip("|")
        if current_key == last_key:
            # Extend the current item until this observation.
            last["last_seen"] = iso(now)
            last["bitrate"] = info["bitrate"]
            last["listeners"] = info["listeners"]
            save_history(history)
            return history

    history.append({
        "start": iso(now),
        "last_seen": iso(now),
        "artist": info["artist"],
        "song": info["song"],
        "bitrate": info["bitrate"],
        "listeners": info["listeners"],
    })

    # Keep roughly the last 7 days of observations.
    cutoff = now - timedelta(days=7)
    history = [x for x in history if datetime.fromisoformat(x["start"].replace("Z", "+00:00")) >= cutoff]
    save_history(history)
    return history

def display_title(item):
    artist, song = item.get("artist", ""), item.get("song", "")
    if artist and song:
        return f"{artist} - {song}"
    return song or artist or "KISS FM Algarve"

def write_m3u(info):
    # Obtém 'utilizador/repositorio' a partir do ambiente do GitHub Actions
    repo = os.getenv("GITHUB_REPOSITORY")

    if repo:
        epg_url = f"https://raw.githubusercontent.com/{repo}/main/{XML_FILE.name}"
    else:
        epg_url = XML_FILE.name  # Salvaguarda para execução local

    lines = [
        f'#EXTM3U x-tvg-url="{epg_url}"',
        f'#EXTINF:-1 tvg-id="{CHANNEL_ID}" tvg-name="{CHANNEL_NAME}" tvg-logo="{LOGO}" radio="true" group-title="Rádios",{CHANNEL_NAME}',
        STREAM,
        ""
    ]
    M3U_FILE.write_text("\n".join(lines), encoding="utf-8")



def write_xmltv(info, history):
    # Make each observed song an EPG event. The last item gets a short future window
    # because the real end time is unknown until the next metadata change.
    tv = ET.Element("tv", {
        "generator-info-name": "KISS FM Algarve GitHub EPG",
        "generator-info-url": "https://kissfm.pt/"
    })
    ch = ET.SubElement(tv, "channel", {"id": CHANNEL_ID})
    ET.SubElement(ch, "display-name").text = CHANNEL_NAME
    ET.SubElement(ch, "icon", {"src": LOGO})

    for i, item in enumerate(history):
        start = datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
        if i + 1 < len(history):
            stop = datetime.fromisoformat(history[i + 1]["start"].replace("Z", "+00:00"))
        else:
            last_seen = datetime.fromisoformat(item["last_seen"].replace("Z", "+00:00"))
            stop = max(last_seen + timedelta(minutes=5), start + timedelta(minutes=1))

        if stop <= start:
            stop = start + timedelta(minutes=1)

        def fmt(d):
            return d.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")

        p = ET.SubElement(tv, "programme", {
            "start": fmt(start),
            "stop": fmt(stop),
            "channel": CHANNEL_ID
        })
        ET.SubElement(p, "title", {"lang": "pt"}).text = display_title(item)
        desc = f"{CHANNEL_NAME} — {item.get('bitrate', 0)} kbps"
        ET.SubElement(p, "desc", {"lang": "pt"}).text = desc

    raw = ET.tostring(tv, encoding="utf-8")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="UTF-8")
    XML_FILE.write_bytes(pretty)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    info = get_info()
    history = update_history(info)
    write_m3u(info)
    write_xmltv(info, history)

    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"KISS FM Algarve")
        print(f"Stream : {info['stream']}")
        print(f"Artist : {info['artist'] or 'Unknown'}")
        print(f"Song   : {info['song'] or 'Unknown'}")
        print(f"Bitrate: {info['bitrate']} kbps")
        print(f"History: {len(history)} events")
        print("Generated: kissfm.m3u, kissfm.xml, data/history.json")

if __name__ == "__main__":
    main()
