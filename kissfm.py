#!/usr/bin/env python3

import argparse
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.dom import minidom


# ============================================================
# KISS FM ALGARVE - V6
# ============================================================

RADIO_NAME = "KISS FM Algarve"
CHANNEL_ID = "kissfm.algarve"

STREAM = "https://nl.digitalrm.pt:8024/stream"
STATUS = "https://nl.digitalrm.pt:8024/status-json.xsl"

SCHEDULE_URL = "https://kissfm.pt/schedule.php?lang=pt"

LOGO = "https://kissfm.pt/player/player_logo.png"

TIMEZONE = ZoneInfo("Europe/Lisbon")

M3U_FILE = Path("kissfm.m3u")
XML_FILE = Path("kissfm.xml")

DATA_DIR = Path("data")
CACHE_FILE = DATA_DIR / "schedule_cache.json"

EPG_DAYS = 14

USER_AGENT = "KISSFM-Kodi-EPG/6.0"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
}


# ============================================================
# UTILIDADES
# ============================================================

def request(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def now_local():
    return datetime.now(TIMEZONE)


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def xmltv_time(dt):
    """
    XMLTV:
    YYYYMMDDHHMMSS +0000
    """
    return dt.astimezone(timezone.utc).strftime(
        "%Y%m%d%H%M%S +0000"
    )


def clean_text(value):
    if not value:
        return ""

    value = html.unescape(value)

    value = re.sub(r"\s+", " ", value)

    return value.strip()


# ============================================================
# ICECAST / NOW PLAYING
# ============================================================

def parse_title(raw):
    raw = clean_text(raw)

    if " - " in raw:
        artist, song = raw.split(" - ", 1)

        return (
            clean_text(artist),
            clean_text(song),
        )

    return "", raw


def get_stream_info():

    try:

        data = json.loads(
            request(STATUS).decode("utf-8", errors="replace")
        )

        ice = data.get("icestats", {})
        source = ice.get("source")

        if isinstance(source, list):

            if not source:
                raise RuntimeError(
                    "Icecast não devolveu nenhum stream."
                )

            source = source[0]

        if not isinstance(source, dict):
            raise RuntimeError(
                "Formato Icecast inesperado."
            )

        raw_title = clean_text(
            source.get("title")
            or source.get("yp_currently_playing")
            or ""
        )

        artist = clean_text(
            source.get("artist")
            or ""
        )

        song = raw_title

        if not artist:
            artist, song = parse_title(raw_title)

        try:

            bitrate = int(
                source.get("bitrate")
                or source.get("ice-bitrate")
                or 0
            )

        except (TypeError, ValueError):

            bitrate = 0

        return {
            "name": RADIO_NAME,
            "id": CHANNEL_ID,
            "stream": STREAM,
            "artist": artist,
            "song": song,
            "title": raw_title,
            "bitrate": bitrate,
            "listeners": source.get("listeners", 0),
            "codec": source.get(
                "server_type",
                "audio/mpeg"
            ),
            "sample_rate": source.get(
                "ice-samplerate",
                ""
            ),
            "channels": source.get(
                "ice-channels",
                ""
            ),
            "checked_at": now_utc().isoformat(),
        }

    except Exception as exc:

        print(
            f"Aviso: não foi possível obter metadata: {exc}",
            file=sys.stderr
        )

        return {
            "name": RADIO_NAME,
            "id": CHANNEL_ID,
            "stream": STREAM,
            "artist": "",
            "song": "",
            "title": "",
            "bitrate": 0,
            "listeners": 0,
            "codec": "audio/mpeg",
            "sample_rate": "",
            "channels": "",
            "checked_at": now_utc().isoformat(),
        }


# ============================================================
# PARSE DA GRELHA KISS FM
# ============================================================

def parse_schedule():

    print(
        f"A obter programação: {SCHEDULE_URL}"
    )

    try:

        raw = request(SCHEDULE_URL).decode(
            "utf-8",
            errors="replace"
        )

    except Exception as exc:

        print(
            f"Erro ao obter programação: {exc}",
            file=sys.stderr
        )

        return load_schedule_cache()

    raw = html.unescape(raw)

    programs = []

    # --------------------------------------------------------
    # Procuramos os títulos e horários directamente no HTML.
    #
    # Exemplo:
    #
    # Breakfast Show
    # 08:00 - 12:00
    #
    # --------------------------------------------------------

    pattern = re.compile(
        r"""
        >
        \s*
        ([^<>]{2,100}?)
        \s*
        (?:
            &nbsp;
            |
            \s*
        )
        <
        /h2
        >
        """,
        re.I | re.X
    )

    # Primeiro método: procurar h2 completos.
    h2_matches = re.findall(
        r"<h2[^>]*>(.*?)</h2>",
        raw,
        re.I | re.S
    )

    for block in h2_matches:

        block_clean = re.sub(
            r"<[^>]+>",
            " ",
            block
        )

        block_clean = clean_text(block_clean)

        match = re.search(
            r"(.+?)\s+(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})",
            block_clean
        )

        if not match:
            continue

        name = clean_text(match.group(1))
        start = match.group(2)
        stop = match.group(3)

        # Evitar lixo HTML.
        if not name:
            continue

        # Remover possíveis restos.
        name = re.sub(
            r"\s+",
            " ",
            name
        ).strip()

        # Alguns nomes podem ter lixo antes.
        name = name.replace(
            "\xa0",
            " "
        ).strip()

        programs.append({
            "name": name,
            "start": start,
            "stop": stop,
        })

    # --------------------------------------------------------
    # Fallback: procurar directamente horários no HTML.
    # --------------------------------------------------------

    if not programs:

        times = re.findall(
            r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})",
            raw
        )

        known_names = [
            "Breakfast Show",
            "Let’s Do Lunch",
            "Let's Do Lunch",
            "Drive Time",
            "GHR",
            "Solid Gold Sunday",
        ]

        for start, stop in times:

            for name in known_names:

                if name.lower() in raw.lower():

                    programs.append({
                        "name": name,
                        "start": start,
                        "stop": stop,
                    })

                    break

    # --------------------------------------------------------
    # Normalizar nomes
    # --------------------------------------------------------

    normalized = []

    aliases = {
        "Let's Do Lunch": "Let’s Do Lunch",
    }

    for p in programs:

        name = aliases.get(
            p["name"],
            p["name"]
        )

        name = clean_text(name)

        if not name:
            continue

        normalized.append({
            "name": name,
            "start": p["start"],
            "stop": p["stop"],
        })

    # --------------------------------------------------------
    # Remover duplicados
    # --------------------------------------------------------

    unique = []

    seen = set()

    for p in normalized:

        key = (
            p["name"],
            p["start"],
            p["stop"],
        )

        if key in seen:
            continue

        seen.add(key)

        unique.append(p)

    # --------------------------------------------------------
    # Associar dias da semana.
    #
    # Python:
    # 0 = Segunda
    # 1 = Terça
    # 2 = Quarta
    # 3 = Quinta
    # 4 = Sexta
    # 5 = Sábado
    # 6 = Domingo
    # --------------------------------------------------------

    result = []

    for p in unique:

        name = p["name"]

        if name in (
            "Breakfast Show",
            "Let’s Do Lunch",
            "Drive Time",
        ):

            days = [0, 1, 2, 3, 4]

        elif name == "GHR":

            days = [5]

        elif name == "Solid Gold Sunday":

            days = [6]

        else:

            days = list(range(7))

        result.append({
            "name": name,
            "start": p["start"],
            "stop": p["stop"],
            "days": days,
        })

    if result:

        save_schedule_cache(result)

    return result


# ============================================================
# CACHE
# ============================================================

def save_schedule_cache(programs):

    try:

        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        CACHE_FILE.write_text(
            json.dumps(
                programs,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    except Exception as exc:

        print(
            f"Aviso: não foi possível guardar cache: {exc}",
            file=sys.stderr
        )


def load_schedule_cache():

    if not CACHE_FILE.exists():

        print(
            "Não existe cache de programação.",
            file=sys.stderr
        )

        return []

    try:

        programs = json.loads(
            CACHE_FILE.read_text(
                encoding="utf-8"
            )
        )

        print(
            "A utilizar programação guardada em cache."
        )

        return programs

    except Exception as exc:

        print(
            f"Erro ao ler cache: {exc}",
            file=sys.stderr
        )

        return []


# ============================================================
# GERAR INSTÂNCIAS EPG
# ============================================================

def generate_epg(programs):

    today = now_local().date()

    result = []

    for offset in range(EPG_DAYS):

        current_date = today + timedelta(
            days=offset
        )

        weekday = current_date.weekday()

        for program in programs:

            if weekday not in program["days"]:
                continue

            start_hour, start_minute = map(
                int,
                program["start"].split(":")
            )

            stop_hour, stop_minute = map(
                int,
                program["stop"].split(":")
            )

            start_dt = datetime.combine(
                current_date,
                time(
                    start_hour,
                    start_minute
                ),
                tzinfo=TIMEZONE
            )

            # ------------------------------------------------
            # IMPORTANTE:
            #
            # GHR 22:00 - 00:00
            #
            # O fim é no dia seguinte.
            # ------------------------------------------------

            stop_date = current_date

            if (
                stop_hour < start_hour
                or (
                    stop_hour == start_hour
                    and stop_minute <= start_minute
                )
            ):

                stop_date += timedelta(days=1)

            stop_dt = datetime.combine(
                stop_date,
                time(
                    stop_hour,
                    stop_minute
                ),
                tzinfo=TIMEZONE
            )

            result.append({
                "name": program["name"],
                "start": start_dt,
                "stop": stop_dt,
            })

    # --------------------------------------------------------
    # Ordenação
    # --------------------------------------------------------

    result.sort(
        key=lambda x: x["start"]
    )

    # --------------------------------------------------------
    # Remover duplicados
    # --------------------------------------------------------

    unique = []

    seen = set()

    for item in result:

        key = (
            item["name"],
            item["start"],
            item["stop"],
        )

        if key in seen:
            continue

        seen.add(key)

        unique.append(item)

    return unique


# ============================================================
# XMLTV
# ============================================================

def write_xmltv(epg):

    tv = ET.Element(
        "tv",
        {
            "generator-info-name":
                "KISS FM Algarve GitHub EPG 6.0",

            "generator-info-url":
                "https://kissfm.pt/",
        }
    )

    channel = ET.SubElement(
        tv,
        "channel",
        {
            "id": CHANNEL_ID
        }
    )

    ET.SubElement(
        channel,
        "display-name"
    ).text = RADIO_NAME

    ET.SubElement(
        channel,
        "icon",
        {
            "src": LOGO
        }
    )

    for item in epg:

        programme = ET.SubElement(
            tv,
            "programme",
            {
                "start": xmltv_time(
                    item["start"]
                ),

                "stop": xmltv_time(
                    item["stop"]
                ),

                "channel": CHANNEL_ID,
            }
        )

        ET.SubElement(
            programme,
            "title",
            {
                "lang": "pt"
            }
        ).text = item["name"]

    raw = ET.tostring(
        tv,
        encoding="utf-8"
    )

    pretty = minidom.parseString(
        raw
    ).toprettyxml(
        indent="  ",
        encoding="UTF-8"
    )

    XML_FILE.write_bytes(
        pretty
    )


# ============================================================
# M3U
# ============================================================

def get_epg_url():

    repository = os.getenv(
        "GITHUB_REPOSITORY"
    )

    if repository:

        return (
            "https://raw.githubusercontent.com/"
            f"{repository}/main/{XML_FILE.name}"
        )

    # URL fixa do teu repositório.
    return (
        "https://raw.githubusercontent.com/"
        "ZincoZn/m3ukissfm-algarve-/main/"
        "kissfm.xml"
    )


def write_m3u():

    epg_url = get_epg_url()

    lines = [
        f'#EXTM3U x-tvg-url="{epg_url}"',

        (
            '#EXTVLCOPT:http-user-agent='
            f'"{USER_AGENT}"'
        ),

        (
            '#EXTINF:-1 '
            f'tvg-id="{CHANNEL_ID}" '
            f'tvg-name="{RADIO_NAME}" '
            f'tvg-logo="{LOGO}" '
            'radio="true" '
            'is-radio="true" '
            'group-title="Rádios",'
            f'{RADIO_NAME}'
        ),

        STREAM,

        "",
    ]

    M3U_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# ============================================================
# MOSTRAR PROGRAMAÇÃO
# ============================================================

def print_schedule(programs):

    print()
    print(
        "PROGRAMAS ENCONTRADOS:"
    )
    print(
        "=" * 60
    )

    day_names = [
        "Segunda",
        "Terça",
        "Quarta",
        "Quinta",
        "Sexta",
        "Sábado",
        "Domingo",
    ]

    for p in programs:

        days = ",".join(
            str(x)
            for x in p["days"]
        )

        print(
            f'{p["name"]} | '
            f'{p["start"]} - '
            f'{p["stop"]} | '
            f'dias={days}'
        )

    print()


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "KISS FM Algarve - "
            "M3U + XMLTV para Kodi"
        )
    )

    parser.add_argument(
        "--schedule",
        action="store_true",
        help="mostrar programação encontrada"
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="mostrar metadata em JSON"
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # 1. Obter programação
    # --------------------------------------------------------

    programs = parse_schedule()

    if not programs:

        print(
            "ERRO: não foi possível obter a programação.",
            file=sys.stderr
        )

        sys.exit(1)

    if args.schedule:

        print_schedule(programs)

    # --------------------------------------------------------
    # 2. Gerar EPG
    # --------------------------------------------------------

    epg = generate_epg(programs)

    # --------------------------------------------------------
    # 3. Obter metadata
    # --------------------------------------------------------

    info = get_stream_info()

    # --------------------------------------------------------
    # 4. Gerar ficheiros
    # --------------------------------------------------------

    write_xmltv(epg)

    write_m3u()

    # --------------------------------------------------------
    # 5. JSON
    # --------------------------------------------------------

    if args.json:

        print(
            json.dumps(
                info,
                ensure_ascii=False,
                indent=2
            )
        )

        return

    # --------------------------------------------------------
    # 6. Resultado
    # --------------------------------------------------------

    print(
        "KISS FM Algarve"
    )

    print(
        "========================================"
    )

    print(
        f"Stream : {info['stream']}"
    )

    print(
        f"Artist : "
        f"{info['artist'] or 'Unknown'}"
    )

    print(
        f"Song   : "
        f"{info['song'] or 'Unknown'}"
    )

    print(
        f"Bitrate: "
        f"{info['bitrate']} kbps"
    )

    print(
        f"EPG   : "
        f"{len(epg)} programas"
    )

    print()

    print(
        "Generated:"
    )

    print(
        "  kissfm.m3u"
    )

    print(
        "  kissfm.xml"
    )


if __name__ == "__main__":
    main()
